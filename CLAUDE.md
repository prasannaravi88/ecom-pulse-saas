# CLAUDE.md — E-Commerce Profit, Cohort & Paid-Media Intelligence SaaS

## 1. Product Vision & Domain Rules
You are building a lightweight, multi-tenant **E-Commerce Profit, Cohort & Paid-Media Intelligence SaaS** for D2C brands (Shopify / WooCommerce + Meta Ads + Google Ads).
- **Target Customer:** Small-to-mid e-commerce founders and growth marketers who need immediate margin, cohort retention, and ad-efficiency clarity without hiring data engineers.
- **Core Financial Formulas (Enforce Strictly in Backend SQL/Python):**
  - `Net Revenue` = `Gross Revenue - Discounts - Refunds/RTO`
  - `CM1 (Gross Margin)` = `Net Revenue - COGS`
  - `CM2 (Contribution Margin)` = `CM1 - Shipping & Payment Gateway Fees - Ad Spend`
  - `Blended MER` = `Net Revenue / Total Ad Spend`
  - `True ROAS` = `Net Revenue / Attributed Ad Spend` (never rely solely on inflated platform-reported ROAS)
  - `Cohort Retention Rate (Month N)` = `Active Repeat Customers in Month N / Total Customers Acquired in Month 0`

---

## 2. Tech Stack & Architecture
- **Frontend:** Next.js (App Router) + TypeScript + Tailwind CSS + `shadcn/ui` + Recharts (or Tremor/custom SVG heatmaps) + `lucide-react`.
- **Backend & Analytics Engine:** Python `FastAPI` + `PostgreSQL` (with `pgvector` for semantic SKU/campaign search) + `DuckDB` / `pandas` for fast analytical cohort and unit-economics rollups.
- **Multi-Tenancy:** Every core table (`products`, `orders`, `daily_ad_spend`, `alerts`) MUST enforce `tenant_id` isolation on every query.
- **Currency Support:** Support `INR` (₹, formatted in Lakhs/Crores cleanly) and `USD` ($) per tenant.

---

## 3. Design System (Executive Financial & Growth Precision)
Avoid generic purple AI templates, cluttered rainbow charts, and heavy drop shadows.
- **Theme:** Crisp, high-contrast financial workspace — Light mode default (`#F8F8F6` warm slate/paper canvas, `#FFFFFF` elevated cards, `#111214` primary ink, `#5E626A` muted ink, `#E2E4E9` `1px` architectural borders) with semantic financial accents:
  - Positive / Margin Lift: Deep Emerald (`#15803D`)
  - Risk / RTO / CAC Spike: Terracotta Red (`#B91C1C`)
  - Primary Interactive Accent: Cobalt Slate (`#1D4ED8`)
- **Typography:** `Plus Jakarta Sans` (UI & headings) + `JetBrains Mono` (`tabular-nums` for all financial figures, ROAS, CAC, and cohort percentages).

---

## 4. MVP Feature Requirements (v1)
1. **Executive P&L & Unit Economics Overview:**
   - Date-range selector (`7D`, `30D`, `90D`, `QTD`) + Currency display (`₹ INR` / `$ USD`).
   - Top KPI strip: Net Revenue, Ad Spend, Contribution Margin (`CM2` ₹/$ and %), Blended MER, and Blended CAC—each with period-over-period delta badges.
   - Stacked waterfall or daily trend chart breaking down Gross Sales → Discounts/RTO → COGS/Shipping → Ad Spend → Net Contribution Profit.
2. **Interactive Cohort Retention & LTV Payback Heatmap:**
   - Acquisition month rows (`M0` through `M6+`) showing cohort size, CAC, `M0–M6` repeat retention % heatmap cells, and cumulative LTV vs. CAC payback indicator.
   - Toggle between **Retention %** and **Cumulative LTV (₹/$)**.
3. **SKU & Channel Efficiency Guardrails:**
   - Sortable table surfacing `Hero SKUs` (high repeat rate + healthy CM2) vs. `Margin Leaks` (high ad spend + high refund/RTO rate or negative CM2).
4. **CSV / Shopify Export Ingestion + Realistic D2C Seed Dataset:**
   - Seed the database on startup with 6 months of realistic D2C e-commerce data (prepaid vs. COD orders, festive spike, realistic RTO/refund rates, 12 SKUs, Meta & Google daily spend) so the app works immediately out of the box.
   - Include a working **Upload Orders / Ad Spend CSV** drawer that validates columns, ingests rows into the active tenant, and recomputes metrics live.
5. **Guardrailed AI Analyst Drawer (Read-Only Semantic Queries):**
   - Slide-over analyst panel with suggested executive prompts (*"Which SKUs are leaking contribution margin this month?"*, *"How fast do festive cohorts pay back CAC?"*, *"Compare Meta vs. Google True ROAS after refunds"*).
   - Queries ONLY pre-aggregated canonical views/endpoints—never executes arbitrary write SQL.

---

## 5. Build & Verification Protocol
1. **Fetch Current Docs:** Use Context7 MCP when configuring FastAPI, DuckDB/SQLAlchemy, or Next.js App Router patterns.
2. **Modular Monorepo Layout:**
   - `/frontend` (Next.js app)
   - `/backend` (FastAPI app, SQL migrations/models, seed generator, cohort & margin calculation service, CSV ingestion endpoint, guardrailed analyst endpoint)
   - `docker-compose.yml` (PostgreSQL with `pgvector` + backend + frontend, plus local dev scripts so both services run easily without Docker if preferred).
3. **Mandatory Playwright Audit:** Before finishing, start both backend and frontend, verify API responses return real computed numbers (no empty states or `NaN`), inspect Desktop (`1440x900`) and Mobile (`390x844`) screenshots via Playwright MCP, and confirm zero console or network errors.