# LUMEN backend

FastAPI service: deterministic market-intelligence engine plus the product
layer around it.

## Status

**Implemented and tested (282 tests, no network/database/credentials required):**

- Deterministic intelligence engine (unchanged from its original build)
- PostgreSQL schema, 19 tables, one reproducible Alembic migration
- Google-ID-token authentication with LUMEN-issued session tokens; guest sessions
- Provider abstraction returning raw payloads + a separate normalization layer
- Replay provider: 6 scenarios, 50-instrument universe, virtual clock
- `/api/v1` surface: auth, dashboard, watchlists, stocks, events, behavior,
  replay, notifications, protected debug, WebSocket
- Behaviour tracking, aggregated affinity, capped personalization
- Groq explanation service with validated deterministic fallback
- Since-You-Were-Away digest with baseline resolution and day drilldown
- Notifications, temporary mutes, 90-day retention

**Not implemented:**

- Live Upstox calls (no account/credentials; client written but unverified)
- Upstox WebSocket ingestion (REST polling path only)
- News ingestion (interface exists; replay supplies predefined events)
- Background scheduler (cycles run on request; no APScheduler loop yet)

## Setup

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows
.venv/Scripts/python -m uvicorn app.main:app --reload
.venv/Scripts/python -m pytest
```

No `.env` is required. Without `DATABASE_URL` the product routes return a clear
503 while `/health` and `/intelligence/demo` keep working.

## Architecture

```
app/
├── intelligence/     THE ENGINE - pure, no I/O, unchanged. Source of truth.
├── providers/        base (Protocol, RAW payloads) | replay | upstox | registry
├── market/           calendar | clock | freshness | normalization | context
├── replay/           universe (50 instruments) | scenarios (6)
├── services/         pipeline, events, watchlists, behavior, digest,
│                     dashboard, explanation, notifications, retention
├── api/v1/           transport only - no business logic
├── schemas/          typed request/response models
├── auth/             google verification | session tokens | dependencies
└── db/               Base | models | session
```

Dependency direction is one-way: `intelligence/` imports nothing from the
product layer. `providers/` return raw vendor payloads; `market/normalization`
is the only module that understands a vendor's response shape.

## Key decisions

**Provider interface returns raw payloads.** Normalization lives in exactly one
place, so adding a provider means adding a parser, not touching the engine.
The replay provider emits Upstox-shaped payloads, so the whole chain is
exercised by tests without credentials.

**Market-wide movement is classification, not a score adjustment.** The engine
already handles it deterministically through the relative-to-NIFTY and
relative-to-sector families - a stock that falls only as far as its index earns
nothing from them. Adding a second correction would double-count and move the
source of truth out of the engine. `market/context.py` therefore adds breadth
detection and a stock-specific / sector-wide / market-wide label, and changes
no score.

**Personalization is contained by construction.** Behaviour becomes a single
0-100 number handed to the engine's existing `behavioral_relevance` parameter,
which caps its effect at 8 points and ignores it below the meaningfulness
floor. No code outside the engine can reorder by anything else.

**The LLM cannot affect correctness.** The deterministic explanation is computed
and stored first. Groq only rephrases an already-established fact bundle that
excludes the score, and its output is rejected in code if it contains advice,
unestablished causation, or a score. Every rejection falls back to the
deterministic text.

**The digest baseline is never advanced by rendering.** Precedence is manual >
reviewed digest > previous visit > last observation > first-use anchor.
`previous_visit_at` only moves after a real absence (>30 minutes), so
refreshing cannot destroy the digest a user returned to read.

**Replay never pretends to be live.** Mode is an explicit parameter. A live
request with no credentials returns `degraded: true` and a warning rather than
synthetic data. Every replay payload carries `is_replay` and the label
"Demo / Replay".

## Data

PostgreSQL holds product and derived data. Bulk raw market history does not go
in it: replay is generated from declarative scenario parameters, and only
normalized observations the engine actually reasoned about are persisted, under
a 90-day retention window (`services/retention.py`).

Models use generic SQLAlchemy types (`JSON`, no `ARRAY`), so the same metadata
and the same migration run on PostgreSQL in production and SQLite in tests.

```bash
.venv/Scripts/python -m alembic upgrade head    # requires DATABASE_URL
```

The migration was generated and verified (upgrade, downgrade, re-upgrade)
against SQLite. **It has not been run against real PostgreSQL or Supabase** -
no server was available on this machine.

## Replay scenarios

| Key | Demonstrates |
|---|---|
| `crash_stable_market` | Stock-specific crash: Critical, all excess return |
| `market_wide_selloff` | ~4% falls everywhere, **zero events** - the point of the product |
| `earnings_beat_volume` | +2.2% on 4.6x volume + news: small move that matters |
| `gap_up_news` | Gap judged against the stock's own gap distribution |
| `sector_rotation` | Below the index but in line with sector: sector-wide |
| `quiet_session` | Nothing surfaced - the threshold is real |

**Honesty note:** the 50-instrument universe uses real NSE symbols, sectors and
plausible price levels, but its statistical baselines are derived from a fixed
seed, not downloaded from an exchange - this machine has no market-data access.
`replay/universe.py` returns exactly the shape
`market/normalization.daily_baselines()` produces from real Upstox candles, so
substituting real history is a one-function change.

## Security

- User data is isolated by authenticated user; every service takes a `User` and
  filters by `user.id`. Tests assert cross-user access fails.
- `/api/v1/debug/intelligence` returns 404 unless `DEBUG_API_TOKEN` is set, then
  requires it as `X-Lumen-Debug-Token`. It reports capabilities as booleans only.
- `/health` and the debug endpoint never return credential values; a test
  asserts this across endpoints.
