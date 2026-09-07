# Deploying LUMEN

Everything below uses free tiers only.

```
Browser  →  Vercel (Next.js frontend)  →  Render (FastAPI backend)  →  Supabase (PostgreSQL)
```

**Before you start you need:** a GitHub account with this repository pushed to
it, and free accounts on [supabase.com](https://supabase.com),
[render.com](https://render.com) and [vercel.com](https://vercel.com). Google
sign-in and Groq are optional — LUMEN works fully without both.

Total time: roughly 30–45 minutes, most of it waiting for builds.

---

## Step 1 — Create the database (Supabase)

1. Go to [supabase.com](https://supabase.com) and sign in.
2. Click **New project**.
3. Fill in:
   - **Name**: `lumen`
   - **Database Password**: click Generate, then **copy it somewhere safe** —
     you cannot see it again and you need it in the next step.
   - **Region**: pick the one closest to you (e.g. Mumbai / South Asia).
4. Click **Create new project** and wait ~2 minutes while it provisions.

## Step 2 — Get the connection string

1. In your project, click the **Connect** button in the top bar (older layouts:
   **Project Settings → Database**).
2. Choose the **Session pooler** connection string. It looks like:

   ```
   postgresql://postgres.abcdefgh:[YOUR-PASSWORD]@aws-0-ap-south-1.pooler.supabase.com:5432/postgres
   ```

3. Replace `[YOUR-PASSWORD]` with the password from Step 1.
4. **Change the scheme** from `postgresql://` to `postgresql+psycopg://` so
   SQLAlchemy uses the psycopg 3 driver this project installs:

   ```
   postgresql+psycopg://postgres.abcdefgh:yourpassword@aws-0-ap-south-1.pooler.supabase.com:5432/postgres
   ```

This finished string is your `DATABASE_URL`. Keep it handy.

> Use the **Session pooler** (port 5432), not the transaction pooler (6543).
> The transaction pooler does not support the prepared statements SQLAlchemy
> issues.

## Step 3 — Create the tables

You can do this from your own machine; it only needs to happen once.

```bash
cd backend
python -m venv .venv

# Windows
.venv\Scripts\pip install -r requirements.txt
set DATABASE_URL=postgresql+psycopg://...your string...
.venv\Scripts\python -m alembic upgrade head

# macOS / Linux
.venv/bin/pip install -r requirements.txt
export DATABASE_URL="postgresql+psycopg://...your string..."
.venv/bin/python -m alembic upgrade head
```

You should see `Running upgrade -> ... initial lumen schema`. In Supabase,
**Table Editor** will now show 19 tables.

(Render also runs this automatically on every deploy, so this step is really
just a way to confirm your connection string works before deploying.)

## Step 4 — Load historical market data

This gives the intelligence engine real baselines — what "normal" volume and
volatility look like for each stock. Without it LUMEN still runs, but scores
against synthetic baselines.

With `DATABASE_URL` still set:

```bash
# Windows
.venv\Scripts\python -m app.scripts.seed_market_data

# macOS / Linux
.venv/bin/python -m app.scripts.seed_market_data
```

It downloads ~180 days of daily history for 50 NSE stocks. Expect 2–5 minutes.

- It is **idempotent** — re-running skips anything already current, so it is
  safe to run again after a failure or a network drop.
- Add `--force` to re-download everything.
- `--symbols RELIANCE,INFY` limits it to specific stocks.

If NSE blocks the request, it automatically falls back to Yahoo Finance. If
both fail, LUMEN still works in Demo/Replay mode.

## Step 5 — Deploy the backend (Render)

1. Go to [render.com](https://render.com) → **New** → **Web Service**.
2. Connect your GitHub account and pick this repository.
3. Render should detect `render.yaml`. If it asks, confirm the settings:
   - **Root Directory**: `backend`
   - **Runtime**: Python
   - **Build Command**: `pip install --upgrade pip && pip install -r requirements.txt`
   - **Start Command**: `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - **Health Check Path**: `/health`
   - **Instance Type**: Free
4. Click **Create Web Service**. The first build takes 5–10 minutes.

### Step 6 — Backend environment variables

In Render: your service → **Environment** → add each of these.

**Required:**

| Key | Value |
|---|---|
| `DATABASE_URL` | your Supabase string from Step 2 |
| `ENVIRONMENT` | `production` |
| `DEBUG` | `false` |
| `SESSION_SECRET` | a long random string (Render can generate one) |

**Add after Step 9, once you know your Vercel URL:**

| Key | Value |
|---|---|
| `CORS_ORIGINS` | `https://your-app.vercel.app` |

**Optional — everything works without these:**

| Key | Purpose |
|---|---|
| `GOOGLE_CLIENT_ID` | enables Google sign-in (demo works regardless) |
| `GROQ_API_KEY` | nicer AI-phrased explanations; falls back to deterministic text |
| `DEBUG_API_TOKEN` | enables `/api/v1/debug/intelligence`; leave unset to keep it disabled |
| `MARKET_PROVIDER_CHAIN` | defaults to `jugaad,yfinance` |

After saving, Render redeploys. Then visit:

```
https://your-service.onrender.com/health
```

You should see `"status": "ok"`. Check the `warnings` array — it will tell you
if anything important is misconfigured.

> **Free tier note:** Render sleeps the service after ~15 minutes idle. The
> next request takes 30–60 seconds to wake it. LUMEN handles this — every
> request rebuilds what it needs — but the first page load after a nap is slow.
> Open the backend URL yourself a minute before demoing.

## Step 7 — Deploy the frontend (Vercel)

1. Go to [vercel.com](https://vercel.com) → **Add New** → **Project**.
2. Import the same GitHub repository.
3. Set **Root Directory** to `frontend`. Vercel auto-detects Next.js for the
   rest — leave build and output settings alone.
4. Before clicking Deploy, expand **Environment Variables** and add:

| Key | Value |
|---|---|
| `NEXT_PUBLIC_API_URL` | `https://your-service.onrender.com` (no trailing slash) |
| `NEXT_PUBLIC_GOOGLE_CLIENT_ID` | your Google client id, or leave blank |

5. Click **Deploy**. Takes 2–4 minutes.
6. Copy your live URL, e.g. `https://lumen-xyz.vercel.app`.

## Step 8 — Connect the two

Go back to Render → **Environment** → set:

```
CORS_ORIGINS = https://lumen-xyz.vercel.app
```

Save. Render redeploys. Without this the browser blocks every API call and the
frontend shows a connection error.

## Step 9 — Google sign-in (optional)

Skip this entirely if you only need the demo.

1. Go to [console.cloud.google.com](https://console.cloud.google.com) → create
   a project.
2. **APIs & Services → OAuth consent screen** → External → fill in an app name
   and your email → Save.
3. **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
4. Application type: **Web application**.
5. Under **Authorised JavaScript origins**, add:
   - `https://lumen-xyz.vercel.app`
   - `http://localhost:3000`
6. Click Create and copy the **Client ID**.
7. Set it in **both** places:
   - Render: `GOOGLE_CLIENT_ID`
   - Vercel: `NEXT_PUBLIC_GOOGLE_CLIENT_ID`
8. Redeploy both.

The backend verifies the Google token itself against Google's public keys and
then issues its own session token — Supabase Auth is not used.

## Step 10 — Test production

Open your Vercel URL and check:

1. The landing page loads with "See what changed."
2. **See what changed** starts a guest session and reaches the demo.
3. Pick **Sharp decline in a calm market**, press **Play 10×** a few times.
   RELIANCE should appear with a Critical score.
4. Open **Why it matters** → the explanation, supporting signals and timeline
   appear.
5. Click **How LUMEN calculated this** → the score breakdown appears.
6. Go to **Dashboard** → "3 stocks deserve your attention" renders.
7. Open a stock → chart, score and market comparison render.
8. **Watchlists** → add and remove a stock.
9. **History** → past events grouped by day.

## Step 11 — Test demo mode specifically

The judge path, which needs no market data and no login:

1. Landing page → **Try LUMEN**.
2. Scenario: **Broad market selloff** → Play to the end.
   Expect **no events** and a market-wide context banner. This is the correct
   result: everything fell together, so nothing is stock-specific.
3. Scenario: **Sharp decline in a calm market** → Play to the end.
   Expect a Critical event on RELIANCE.
4. Scenario: **Quiet session** → Play to the end. Expect nothing.

Every screen must show the **Demo / Replay** label.

---

## Troubleshooting

**"Could not reach the LUMEN backend"**
Render is asleep. Open the backend `/health` URL directly, wait for it to
respond, then reload. If it persists, `CORS_ORIGINS` is probably not set to
your exact Vercel URL (including `https://`, no trailing slash).

**Backend deploy fails on `alembic upgrade head`**
`DATABASE_URL` is wrong. Check you used the Session pooler string, replaced the
password, and changed the scheme to `postgresql+psycopg://`.

**Backend build fails with a Python version error**
`render.yaml` pins `PYTHON_VERSION` to `3.13.1`. If Render does not offer that
exact patch release, change that one value to another 3.13.x, or to `3.12.8` —
the backend uses no version-specific features and its dependencies all publish
wheels for 3.12 and 3.13. It was developed and tested on 3.13.11.

**Dashboard shows "Demo / Replay" when you expected live data**
No market provider was reachable. NSE commonly blocks datacentre IPs, so on
Render the chain usually resolves to Yahoo. If both fail, LUMEN falls back to
replay and says so rather than showing synthetic prices as real.

**Everything works but scores look odd**
You probably skipped Step 4. Run the seed script so baselines come from real
history.

**Google sign-in does nothing**
The client id must be set in both Render and Vercel, and your Vercel URL must
be listed under Authorised JavaScript origins.

---

## Running locally

Two terminals.

**Backend:**

```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # Windows
.venv/Scripts/python -m uvicorn app.main:app --reload
```

Runs on `http://localhost:8000`. No `.env` needed — it starts with no
credentials and no database, and the demo works immediately.

For watchlists and saved events, set `DATABASE_URL` (Supabase or a local
Postgres) and run `alembic upgrade head`.

**Frontend:**

```bash
cd frontend
npm install
cp .env.local.example .env.local     # defaults to http://localhost:8000
npm run dev
```

Runs on `http://localhost:3000`.

---

## Environment variable reference

### Backend (Render)

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `DATABASE_URL` | **yes** in production | — | Supabase Postgres. Must not be SQLite in production. |
| `SESSION_SECRET` | **yes** in production | dev placeholder | Signs LUMEN session tokens. |
| `ENVIRONMENT` | recommended | `development` | Set to `production` to enable config warnings. |
| `DEBUG` | recommended | `true` | Set `false` in production. |
| `CORS_ORIGINS` | **yes** | — | Comma-separated. localhost is always allowed. |
| `GOOGLE_CLIENT_ID` | no | — | Enables Google sign-in. |
| `GROQ_API_KEY` | no | — | AI-phrased explanations; deterministic fallback otherwise. |
| `DEBUG_API_TOKEN` | no | — | Unset means the debug endpoint returns 404. |
| `MARKET_PROVIDER_CHAIN` | no | `jugaad,yfinance` | Ordered fallback chain. |
| `ALLOW_REPLAY_FALLBACK` | no | `true` | Lets the demo work with zero market access. |
| `ENABLE_BACKGROUND_POLLING` | no | `true` | Polls only while NSE is open. |
| `OBSERVATION_RETENTION_DAYS` | no | `90` | Market observation retention. |

### Frontend (Vercel)

| Variable | Required | Purpose |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | **yes** | Backend base URL, no trailing slash. |
| `NEXT_PUBLIC_GOOGLE_CLIENT_ID` | no | Shows the Google button when set. |

**Never commit real values.** `.env` and `.env.local` are gitignored; only the
`.example` files are tracked.
