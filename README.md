# LUMEN

**See what changed. Understand why. Know what matters.**

LUMEN is a market-change intelligence layer for active retail investors.

It is not a watchlist. A watchlist tells you what a stock is worth. LUMEN
answers a different question:

> *"I was away from the market. What changed in the stocks I care about, and
> which 3 deserve my attention now?"*

---

## The idea in one comparison

| | A 4% fall on a day the index fell 4% | A 4% fall on a day the index was flat |
|---|---|---|
| Percent-change list | Top of the list | Top of the list |
| **LUMEN** | **Noise.** Reported as market context, no event raised. | **Critical.** All of it is excess return, on 3× volume, with news nearby. |

Same number, opposite verdict. That distinction is the product.

## Status

| Component | State |
|---|---|
| Intelligence engine | Complete |
| Backend API (46 endpoints) | Complete, verified end-to-end (282 tests) |
| Market data (NSE + Yahoo + replay) | Complete, verified against live data |
| Database schema + migration | Complete (19 tables) |
| Historical seeding | Complete, verified with real NSE history |
| Demo / replay (6 scenarios) | Complete, verified end-to-end |
| Auth (Google + guest) | Complete; Google verified only with an injected verifier |
| Frontend (9 routes) | Complete — builds, lints, typechecks and verified in a real browser |
| Deployment config | Complete (`render.yaml`, `DEPLOYMENT.md`) — never actually deployed |

See [DEPLOYMENT.md](DEPLOYMENT.md) for step-by-step deployment.

## Architecture

```
Browser  →  Vercel (Next.js + TypeScript + Tailwind)
              ↓  REST
         Render (FastAPI)  →  Supabase (PostgreSQL)
              ↓
     NSE (jugaad-data) → Yahoo Finance → deterministic replay
```

**Upstox is not required.** The provider chain falls through automatically, and
replay backstops it, so LUMEN works with no market credentials at all.

## How it decides

Six independent families of evidence, each capped so no single signal can carry
an event alone:

| Family | Max points |
|---|---|
| Price movement (absolute, vs own history, vs own volatility) | 25 |
| Relative performance vs NIFTY and sector | 22 |
| Abnormal volume vs same-time-of-day baseline | 20 |
| Volatility | 12 |
| Opening gap vs the stock's own gap distribution | 10 |
| News | 11 |

Score bands: 0–39 Noise · 40–59 Worth Watching · 60–79 High Attention ·
80–100 Critical. An event needs both a minimum score **and** corroboration from
at least two independent families.

Three rules the code enforces rather than merely intends:

- **Missing data is never zero.** No volatility history is not volatility of
  zero; it lowers the score *and* the reported confidence.
- **The LLM cannot affect correctness.** Detection and scoring are
  deterministic. Groq only rephrases already-established facts, and its output
  is rejected in code if it contains advice, unestablished causation, or a
  score. Everything degrades to deterministic text.
- **No BUY / SELL / HOLD.** Anywhere. Tests assert their absence.

## Run it locally

```bash
# Backend - works with no credentials and no database
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python -m uvicorn app.main:app --reload      # :8000

# Frontend - requires Node.js 20+
cd frontend
npm install
cp .env.local.example .env.local
npm run dev                                                 # :3000
```

Then open <http://localhost:3000> → **See what changed** → pick a scenario.

For watchlists and saved events, set `DATABASE_URL` and run
`.venv/Scripts/python -m alembic upgrade head`.

## Repository

```
lumen/
├── backend/          FastAPI + the intelligence engine (81 files)
│   ├── app/intelligence/   pure, deterministic, no I/O - the source of truth
│   ├── app/providers/      NSE | yfinance | Upstox | replay, behind one interface
│   ├── app/market/         calendar, virtual clock, freshness, normalization
│   ├── app/replay/         50-instrument universe, 6 scenarios
│   ├── app/services/       pipeline, events, digest, behaviour, explanations
│   └── app/api/v1/         46 endpoints
├── frontend/         Next.js App Router (22 files)
├── render.yaml       backend deployment
└── DEPLOYMENT.md     full deployment guide
```

## Honest notes

- **Nothing has been deployed.** The Render/Vercel/Supabase configuration is
  written and reviewed but never executed against those platforms.
- Google OAuth is implemented and unit-tested with an injected verifier, but
  has **never been exercised against real Google credentials**.
- Groq is wired with a validated fallback but has **never been called against
  the real API**; explanations currently come from deterministic templates.
- The replay universe uses real NSE symbols and sectors, but its statistical
  baselines are seed-derived unless you run the seed script against real
  history.
- NSE access via `jugaad-data` is an unofficial scraper and commonly blocked
  from datacentre IPs; on Render the chain will usually resolve to Yahoo.
- Yahoo data is delayed and unofficial. LUMEN never labels it "live" on its own
  merit — freshness is computed from each value's own timestamp.

LUMEN provides market information and analysis, not investment advice.
