import type { Currency } from "./format";
import { getSandboxId } from "./sandbox";

/** Backend origin, inlined at build time from NEXT_PUBLIC_API_URL (see .env.example). */
export const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/+$/, "");

export type RangePreset = "7D" | "30D" | "90D" | "QTD";
export const RANGES: RangePreset[] = ["7D", "30D", "90D", "QTD"];

export interface Tenant { id: string; name: string; currency: Currency }
export interface Kpi { value: number | null; previous: number | null; delta_pct: number | null }
export interface PeriodMeta {
  range: RangePreset | null; start: string; end: string; previous_start: string; previous_end: string; days: number;
}
interface Scoped { tenant: string; currency: Currency; period: PeriodMeta }

export interface WaterfallStep { step: string; kind: "total" | "subtotal" | "decrease"; value: number }
export interface Overview extends Scoped { kpis: Record<string, Kpi>; waterfall: WaterfallStep[] }

export interface DailyRow {
  date: string; gross: number; discounts: number; refunds_rto: number; cogs: number; shipping: number;
  fees: number; ad_spend: number; meta_spend: number; google_spend: number; net_revenue: number;
  cm1: number; cm2: number; orders: number; mer: number | null;
}
export interface Daily extends Scoped { days: DailyRow[] }

export interface ChannelRow {
  channel: string; ad_spend: number; impressions: number; clicks: number; platform_reported_revenue: number;
  platform_roas: number | null; gross_revenue: number; refunds_rto: number; attributed_net_revenue: number;
  true_roas: number | null; roas_inflation: number | null; orders: number; new_customers: number; cac: number | null;
}
export interface Channels extends Scoped { channels: ChannelRow[] }

export type Segment = "hero" | "core" | "margin_leak";
export interface SkuRow {
  sku: string; name: string; category: string | null; units: number; orders: number; gross_revenue: number;
  discounts: number; refunds_rto: number; net_revenue: number; cogs: number; cm1: number; shipping_fees: number;
  ad_spend: number; cm2: number; cm2_pct: number | null; return_rate: number | null; repeat_rate: number | null;
  ad_to_revenue: number | null; true_roas: number | null; segment: Segment; reasons: string[];
}
export interface Skus extends Scoped {
  skus: SkuRow[];
  thresholds: { hero_min_repeat_rate: number; hero_min_cm2_pct: number; leak_min_return_rate: number; leak_min_ad_to_revenue: number };
}

export interface CohortCell {
  month: number; active: number | null; retention: number | null; ltv_net_revenue: number | null;
  ltv_contribution: number | null; partial: boolean; available: boolean;
}
export interface CohortRow {
  cohort_month: string; size: number; ad_spend: number; cac: number | null; cells: CohortCell[];
  payback_month: number | null; ltv_to_cac: number | null;
}
export interface Cohorts { tenant: string; currency: Currency; as_of: string; max_offset: number; cohorts: CohortRow[] }

export interface Fx { base: "USD"; quote: "INR"; rate: number; note: string }

export interface AnalystPrompt { id: string; question: string }
export type CellFormat = "text" | "money" | "pct" | "ratio" | "int" | "month" | "month_offset";
export interface AnalystAnswer {
  tenant: string; currency: Currency; status: "answered" | "refused" | "unsupported"; intent: string | null;
  question: string; title: string; summary: string[]; guardrail: string;
  period: { start: string; end: string }; display_currency: Currency;
  highlights?: { label: string; value: number | null; format: CellFormat; tone?: "positive" | "risk" | null }[];
  table?: { columns: { key: string; label: string; format: CellFormat }[]; rows: Record<string, unknown>[] } | null;
  actions?: string[]; sources?: string[]; suggestions?: AnalystPrompt[];
}

export interface DemoStatus {
  demo_mode: boolean;
  sandbox_ttl_hours: number;
  active_sandboxes: { tenant: string; created_at: string; expires_at: string }[];
}

export interface UploadError { row: number | null; column: string | null; message: string }
export class ApiError extends Error {
  constructor(message: string, public status: number, public detail?: unknown) { super(message); }
}

async function request<T>(path: string, tenant?: string, init?: RequestInit): Promise<T> {
  if (!API_BASE) throw new ApiError("NEXT_PUBLIC_API_URL is not configured", 0);
  const sandbox = getSandboxId();
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        ...(tenant ? { "X-Tenant-ID": tenant } : {}),
        ...(sandbox ? { "X-Sandbox-ID": sandbox } : {}),
        ...init?.headers,
      },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(`Can't reach the API at ${API_BASE}`, 0);
  }
  if (!res.ok) {
    let detail: unknown;
    try { detail = (await res.json()).detail; } catch { /* non-JSON error body */ }
    const msg = typeof detail === "string" ? detail : (detail as { message?: string })?.message;
    throw new ApiError(msg ?? `${res.status} ${res.statusText}`, res.status, detail);
  }
  return res.json() as Promise<T>;
}

const q = (range: RangePreset) => `?range=${range}`;

export const api = {
  tenants: () => request<Tenant[]>("/api/tenants"),
  fx: () => request<Fx>("/api/fx"),
  overview: (t: string, r: RangePreset) => request<Overview>(`/api/metrics/overview${q(r)}`, t),
  daily: (t: string, r: RangePreset) => request<Daily>(`/api/metrics/daily${q(r)}`, t),
  channels: (t: string, r: RangePreset) => request<Channels>(`/api/metrics/channels${q(r)}`, t),
  skus: (t: string, r: RangePreset) => request<Skus>(`/api/metrics/skus${q(r)}`, t),
  cohorts: (t: string) => request<Cohorts>("/api/cohorts", t),
  prompts: () => request<AnalystPrompt[]>("/api/analyst/prompts"),
  ask: (t: string, body: { question?: string; prompt_id?: string; range: RangePreset; display_currency: Currency }) =>
    request<AnalystAnswer>("/api/analyst/ask", t, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }),
  upload: (t: string, kind: "orders" | "ad-spend", file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<Record<string, unknown>>(`/api/ingest/${kind}`, t, { method: "POST", body: form });
  },
  templateUrl: (kind: "orders" | "ad-spend") => `${API_BASE}/api/ingest/templates/${kind}`,
  demoStatus: () => request<DemoStatus>("/api/demo/status"),
  resetDemo: () => request<{ reset: string[] }>("/api/demo/reset", undefined, { method: "POST" }),
};
