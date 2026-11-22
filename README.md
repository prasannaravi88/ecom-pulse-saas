# Ecom Pulse: Profit, Cohort & Paid-Media Intelligence for D2C

Monorepo:

| Path | What |
|---|---|
| `backend/` | FastAPI + SQLAlchemy (PostgreSQL/pgvector, SQLite fallback) + DuckDB/pandas analytics engine |
| `frontend/` | Next.js 16 App Router + TypeScript + Tailwind v4 + shadcn/ui + Recharts |
| `docker-compose.yml` | `db` (pgvector/pg16, host port 5433), `backend` (8000), `frontend` (3000) |

## Run

```bash
docker compose up --build             # everything; seeds two demo tenants on first boot
./scripts/dev-backend.sh              # API without Docker (SQLite at backend/ecom_pulse.db)
./scripts/dev-frontend.sh             # Next.js dev server
./scripts/test-backend.sh [--pg]      # pytest in the backend image (SQLite, or also Postgres)
```

Every data endpoint needs the `X-Tenant-ID` header (or `?tenant=`). Demo tenants:
`lumiere-botanicals` (INR skincare, COD-heavy) and `northbound-coffee` (USD, prepaid).

## Frontend

Single dashboard at `/` (`frontend/src/components/dashboard/`). The browser calls the API at
`NEXT_PUBLIC_API_URL` (inlined at build time; for local dev, `cp frontend/.env.example frontend/.env.local`), so the
backend's `ALLOWED_ORIGINS` must include the frontend origin. Production deployment: see `DEPLOYMENT.md`.
Tenant, range and display currency live in the URL (`?tenant=&range=90D&ccy=USD`).

- **Top bar:** tenant switcher, ₹ INR / $ USD display toggle (fixed `USD_INR_RATE`, default 84, from
  `GET /api/fx`; books stay in the tenant's currency), 7D / 30D / 90D / QTD, Upload CSV and Analyst drawers.
- **KPI strip:** Net Revenue, Ad Spend, CM2 (amount and %), Blended MER, Blended CAC, each with a
  period-over-period delta coloured by whether the move is good (CAC up = red).
- **P&L chart:** horizontal waterfall from gross sales to CM2, or a daily trend (net revenue bars, CM2 and ad spend lines).
- **Cohort heatmap:** M0–M6 retention % (one-hue ramp) or cumulative LTV, by contribution (diverging
  around CAC, ✓ once paid back) or net revenue. Hover or tab through cells for detail.
- **Guardrails:** sortable SKU table with Hero / Margin-leak / Core filter, and a channel table with
  platform vs True ROAS.
- **Analyst:** `GET /api/analyst/prompts` + `POST /api/analyst/ask`. Questions are routed by keyword to
  six fixed read-only intents over the same rollups the dashboard uses; write-like or raw-SQL input is refused.

## API

| Endpoint | Returns |
|---|---|
| `GET /api/tenants` | tenants + currency |
| `GET /api/metrics/overview?range=7D\|30D\|90D\|QTD` | KPI strip with previous-period deltas + P&L waterfall |
| `GET /api/metrics/daily?range=` | daily gross → net → CM1 → CM2 trend, Meta/Google spend |
| `GET /api/metrics/channels?range=` | Meta vs Google: spend, platform ROAS, True ROAS, CAC |
| `GET /api/metrics/skus?range=` | SKU unit economics + `hero` / `core` / `margin_leak` segment |
| `GET /api/cohorts` | M0–M6 retention %, cumulative LTV (net revenue & contribution), CAC, payback month |
| `GET /api/metrics/payment-mix?range=` | COD vs prepaid orders, RTO rate, RTO logistics cost |
| `GET /api/fx` | display conversion rate for the ₹/$ toggle |
| `GET /api/analyst/prompts`, `POST /api/analyst/ask` | read-only analyst (`{question \| prompt_id, range, display_currency}`) |
| `GET /api/alerts` | margin-leak guardrail alerts |
| `GET /api/demo/status`, `POST /api/demo/reset` | Live Sandbox state / discard this browser's sandbox (`X-Sandbox-ID`) |
| `POST /api/ingest/orders`, `POST /api/ingest/ad-spend` | multipart CSV upload, all-or-nothing validation |
| `GET /api/ingest/templates/{orders,ad-spend}`, `GET /api/ingest/schema` | CSV templates and accepted columns/aliases |

Custom windows: `?start=YYYY-MM-DD&end=YYYY-MM-DD`. "Today" for presets is the tenant's latest data date.

## Accounting conventions

Formulas live in `backend/app/services/finance.py` (CLAUDE.md §1); the DuckDB rollups in
`analytics.py` apply them and the tests check both against hand-worked ledgers.

- **Cancelled** orders are excluded. **RTO** reverses the full line (gross − discount) into Refunds/RTO;
  goods return to stock (COGS 0) but forward + return shipping and fees still count.
  **Refunded** lines reverse `refund_amount`; goods are written off (COGS kept).
- Order-level shipping/fees are allocated to lines by gross share. Ad spend mapped to a SKU
  (`daily_ad_spend.sku`) is direct; unmapped spend is spread by SKU net revenue, so SKU CM2 sums to tenant CM2.
- True ROAS = net revenue of orders attributed to the channel ÷ that channel's spend.
- Cohorts are by first-order month. A customer is active in month N if they placed a non-RTO order;
  M0 = 100%. Cohort CAC = acquisition-month spend ÷ cohort size. Payback = first month where
  cumulative contribution LTV (net − COGS − shipping − fees) ≥ CAC.
