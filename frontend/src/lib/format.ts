export type Currency = "INR" | "USD";

const SYMBOL: Record<Currency, string> = { INR: "₹", USD: "$" };

/** Convert between tenant and display currency with a single static USD→INR rate. */
export function convert(value: number, from: Currency, to: Currency, usdInr: number): number {
  if (from === to) return value;
  return from === "INR" ? value / usdInr : value * usdInr;
}

/** Compact money: ₹12.40 L / ₹1.20 Cr for INR (Indian grouping), $12.4K / $1.20M for USD. */
export function formatMoney(value: number | null | undefined, currency: Currency, compact = true): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const sign = value < 0 ? "−" : "";
  const abs = Math.abs(value);
  const sym = SYMBOL[currency];
  if (!compact || abs < 1e3) {
    const locale = currency === "INR" ? "en-IN" : "en-US";
    const digits = currency === "USD" && abs < 100 ? 2 : 0;
    return `${sign}${sym}${abs.toLocaleString(locale, { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
  }
  if (currency === "INR") {
    if (abs >= 1e7) return `${sign}${sym}${(abs / 1e7).toFixed(2)} Cr`;
    if (abs >= 1e5) return `${sign}${sym}${(abs / 1e5).toFixed(2)} L`;
    return `${sign}${sym}${(abs / 1e3).toFixed(1)}K`;
  }
  if (abs >= 1e6) return `${sign}${sym}${(abs / 1e6).toFixed(2)}M`;
  return `${sign}${sym}${(abs / 1e3).toFixed(1)}K`;
}

/** Axis ticks: short, clean, one style per axis. */
export function formatAxisMoney(value: number, currency: Currency): string {
  const abs = Math.abs(value);
  const sign = value < 0 ? "−" : "";
  const sym = SYMBOL[currency];
  if (currency === "INR") {
    if (abs >= 1e7) return `${sign}${sym}${+(abs / 1e7).toFixed(1)}Cr`;
    if (abs >= 1e5) return `${sign}${sym}${+(abs / 1e5).toFixed(1)}L`;
  } else if (abs >= 1e6) return `${sign}${sym}${+(abs / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${sign}${sym}${+(abs / 1e3).toFixed(abs < 1e4 ? 1 : 0)}K`;
  return `${sign}${sym}${abs.toFixed(0)}`;
}

export function formatPct(value: number | null | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function formatRatio(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${value.toFixed(2)}x`;
}

export function formatInt(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return Math.round(value).toLocaleString("en-US");
}

export function formatMonth(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  return d.toLocaleDateString("en-US", { month: "short", year: "2-digit" });
}

export function formatDay(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

/** Evenly spaced ticks on 1 / 2 / 2.5 / 5 × 10ⁿ steps covering [min, max], always including 0. */
export function niceTicks(min: number, max: number, target = 5): number[] {
  const lo = Math.min(0, min), hi = Math.max(0, max);
  const span = hi - lo || 1;
  const raw = span / (target - 1);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = ([1, 2, 2.5, 5, 10].find((m) => m * mag >= raw) ?? 10) * mag;
  const ticks: number[] = [];
  for (let v = Math.floor(lo / step) * step; v <= Math.ceil(hi / step) * step + step / 2; v += step) ticks.push(+v.toFixed(6));
  return ticks;
}
