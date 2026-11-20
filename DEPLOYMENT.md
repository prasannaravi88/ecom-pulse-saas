# Deployment checklist

Frontend (`/frontend`) on **Vercel**. Backend (`/backend`) plus PostgreSQL on **Render** or **Railway**.
Deploy the backend first, because the frontend build needs its URL.

```
Browser ──► Vercel (Next.js static/edge) ──(fetch, CORS)──► FastAPI on Render/Railway ──► PostgreSQL
            NEXT_PUBLIC_API_URL                              ALLOWED_ORIGINS, DATABASE_URL, DEMO_MODE
```

## 1. Environment variables

### Backend (Render / Railway)

| Variable | Required | Example | Notes |
|---|---|---|---|
| `DATABASE_URL` | yes | `postgresql://user:pass@host:5432/db` | `postgres://` and `postgresql://` are converted to the psycopg 3 driver automatically. |
| `ALLOWED_ORIGINS` | yes | `https://ecom-pulse.vercel.app,https://pulse.example.com` | Comma-separated exact origins: scheme + host, no path. A trailing `/` is ignored. If it's empty, browsers are blocked. |
| `ALLOWED_ORIGIN_REGEX` | no | `https://ecom-pulse-[a-z0-9-]+-yourteam\.vercel\.app` | Allows Vercel preview deployments, whose URLs change per branch. |
| `DEMO_MODE` | for the public demo | `true` | Seeded tenants become read-only. Visitor uploads go to a private per-browser sandbox, and the UI shows the Live Sandbox badge and Reset Demo. |
| `SEED_ON_STARTUP` | no (default `true`) | `true` | Seeds Lumière Botanicals (INR) and Northbound Coffee Co. (USD) only if they don't exist yet. |
| `SANDBOX_TTL_HOURS` | no (default `24`) | `24` | Visitor sandboxes expire after this and are cleaned up at startup and whenever a new sandbox is created. |
| `MAX_SANDBOXES` | no (default `200`) | `200` | Cap on concurrent sandboxes. The oldest is evicted first, so the database size stays bounded. |
| `USD_INR_RATE` | no (default `84`) | `84` | Display-only rate for the ₹/$ toggle. |
| `PORT` | set by platform | n/a | The Dockerfile binds to it. Don't set it yourself on Render or Railway. |

### Frontend (Vercel)

| Variable | Required | Example | Notes |
|---|---|---|---|
| `NEXT_PUBLIC_API_URL` | yes | `https://ecom-pulse-api.onrender.com` | Backend public URL, no trailing slash. It's inlined at build time, so redeploy after changing it. `next build` fails on purpose if it's missing. |

## 2. Backend

The image is `backend/Dockerfile`: Python 3.12, non-root user, one uvicorn worker on `$PORT`, with a health check at `/api/health`.

> Keep it to **one instance and one worker**. Analytics engines are cached in memory per tenant and invalidated on upload, so a second instance would keep serving pre-upload numbers. Scale up the instance size, not the instance count.

The first boot seeds about 40k demo orders, which takes roughly 15–30 s. Health checks pass once seeding is done.

### Option A: Render (Blueprint, `render.yaml`)

1. Push the repo to GitHub or GitLab.
2. In Render, go to **New → Blueprint** and select the repo. It creates `ecom-pulse-api` (Docker) and `ecom-pulse-db` (Postgres 16).
3. When prompted, set `ALLOWED_ORIGINS` to your Vercel URL. You can fill it in after step 4 and redeploy. `ALLOWED_ORIGIN_REGEX` is optional.
4. Wait for the deploy, then open `https://<service>.onrender.com/api/health`. It should return `{"status":"ok"}`.
5. Plans: `0.5c-512mb` web and `0.1c-256mb` Postgres. Both can be switched to `free` for a demo, but a free web service sleeps when idle, and Render deletes free databases after 30 days.

### Option B: Railway (dashboard)

Railway's `railway.toml` config-as-code is deprecated: existing files stop being read on 2026-12-01 and new services can't use it. Configure the service in the dashboard instead:

1. **New Project → Deploy PostgreSQL.** Railway's default Postgres has **no pgvector**. The backend detects this, logs `pgvector unavailable; storing embeddings as JSON`, and runs normally. If you want pgvector, deploy Railway's **pgvector** template instead.
2. **New → GitHub Repo** and select this repo. In the service **Settings**:
   - **Root Directory:** `backend`. Railway then builds `backend/Dockerfile` automatically.
   - **Healthcheck Path:** `/api/health`. Set the timeout to 300 s to cover first-boot seeding.
   - **Restart policy:** On failure.
   - **Replicas:** 1.
3. In **Variables**, set:
   - `DATABASE_URL` = `${{Postgres.DATABASE_URL}}` (a reference variable; use the internal URL)
   - `ALLOWED_ORIGINS` = your Vercel URL
   - `DEMO_MODE` = `true`
   - Optionally `ALLOWED_ORIGIN_REGEX` and `SANDBOX_TTL_HOURS`
4. Under **Networking → Generate Domain**, then check `https://<app>.up.railway.app/api/health`.

## 3. Frontend on Vercel

1. **Add New → Project** and import the repo.
2. Set **Root Directory** to `frontend`. Next.js is auto-detected; the build command is `next build`.
3. In **Environment Variables**, set `NEXT_PUBLIC_API_URL` to the backend URL from step 2 for **Production** and **Preview**.
4. Deploy. Copy the production URL (and the preview URL pattern, if you want previews to work) into the backend's `ALLOWED_ORIGINS` / `ALLOWED_ORIGIN_REGEX`, then **redeploy the backend**, since CORS is read at startup.

## 4. Post-deploy verification

- [ ] `GET <api>/api/health` returns `{"status":"ok"}`.
- [ ] `GET <api>/api/tenants` lists exactly Lumière Botanicals (INR) and Northbound Coffee Co. (USD).
- [ ] `GET <api>/api/demo/status` returns `"demo_mode": true`.
- [ ] Open the Vercel URL. The browser console and the network tab show no CORS errors, and the KPI cards, waterfall, cohort heatmap and SKU table show numbers.
- [ ] The **Live Sandbox** badge is visible. Upload the orders template via **Upload CSV**: the success message says "(in your sandbox)" and the KPIs change.
- [ ] In a private window, the same tenant still shows the clean seed numbers.
- [ ] **Reset Demo** brings your browser back to the seed numbers.
- [ ] Toggle ₹/$ and 7D/30D/90D/QTD, and open the Analyst drawer and ask a question.

## 5. Operations

- **Restore the seed data** (also drops every sandbox), in a Render shell or Railway shell: `python -m app.manage reseed`, **then restart the service**. The running API caches analytics in memory and won't see a reseed done by another process.
- **Remove expired sandboxes now:** `python -m app.manage cleanup-sandboxes`. This also runs automatically at startup and before each new sandbox is created.
- **Real (non-demo) deployment:** set `DEMO_MODE=false`. Uploads then write straight to the tenant, and the badge and Reset Demo are hidden. Set `SEED_ON_STARTUP=false` if you don't want the demo tenants.
- **Local check of the production build:**
  `cd frontend && NEXT_PUBLIC_API_URL=http://localhost:8000 npm run build && npx next start -p 3000`, with the backend from `docker compose up db backend`.
