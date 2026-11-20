"use client";

import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import type { Kpi, Overview } from "@/lib/api";
import { formatPct, formatRatio } from "@/lib/format";
import { cn } from "@/lib/utils";

import { useDisplay } from "./display-context";

type Polarity = "up_good" | "down_good" | "neutral";

function DeltaBadge({ delta, polarity }: { delta: number | null; polarity: Polarity }) {
  if (delta == null) return <span className="text-muted-foreground text-xs">no prior data</span>;
  const flat = Math.abs(delta) < 0.005;
  const good = polarity === "neutral" || flat ? null : (delta > 0) === (polarity === "up_good");
  const Icon = flat ? Minus : delta > 0 ? ArrowUpRight : ArrowDownRight;
  return (
    <span
      className={cn(
        "num inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-xs font-medium",
        good === true && "bg-positive/10 text-positive",
        good === false && "bg-risk/10 text-risk",
        good === null && "bg-muted text-muted-foreground",
      )}
      aria-label={`${delta > 0 ? "up" : "down"} ${formatPct(Math.abs(delta))} vs previous period${good === null ? "" : good ? ", favourable" : ", unfavourable"}`}
    >
      <Icon className="size-3" aria-hidden />
      {formatPct(Math.abs(delta))}
    </span>
  );
}

function Card({ label, value, sub, kpi, polarity, tone }: {
  label: string; value: string; sub?: React.ReactNode; kpi: Kpi; polarity: Polarity; tone?: "positive" | "risk";
}) {
  return (
    <div className="bg-card flex h-full min-w-0 flex-col gap-1.5 p-4">
      <div className="text-muted-foreground text-xs font-medium tracking-wide uppercase">{label}</div>
      <div className={cn("num truncate text-[22px] leading-tight font-semibold",
                         tone === "risk" && "text-risk", tone === "positive" && "text-positive")}>
        {value}
      </div>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <DeltaBadge delta={kpi.delta_pct} polarity={polarity} />
        {sub && <span className="text-muted-foreground num truncate text-xs">{sub}</span>}
      </div>
    </div>
  );
}

export function KpiStrip({ data, loading }: { data: Overview | null; loading: boolean }) {
  const { money } = useDisplay();
  if (!data) {
    return (
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border bg-border lg:grid-cols-5">
        {Array.from({ length: 5 }, (_, i) => (
          <div key={i} className="bg-card p-4"><Skeleton className="h-16 w-full" /></div>
        ))}
      </div>
    );
  }
  const k = data.kpis;
  const cm2 = k.cm2.value ?? 0;
  return (
    <section aria-label="Key metrics" aria-busy={loading}
             className={cn("grid grid-cols-2 gap-px overflow-hidden rounded-lg border bg-border transition-opacity lg:grid-cols-5",
                           loading && "opacity-60")}>
      <Card label="Net Revenue" value={money(k.net_revenue.value)} kpi={k.net_revenue} polarity="up_good"
            sub={`was ${money(k.net_revenue.previous)}`} />
      <Card label="Ad Spend" value={money(k.ad_spend.value)} kpi={k.ad_spend} polarity="neutral"
            sub={`was ${money(k.ad_spend.previous)}`} />
      <div className="col-span-2 lg:col-span-1">
        <Card label="Contribution Margin (CM2)" value={money(k.cm2.value)} kpi={k.cm2} polarity="up_good"
              tone={cm2 < 0 ? "risk" : undefined}
              sub={<>{formatPct(k.cm2_pct.value)} of net · was {formatPct(k.cm2_pct.previous)}</>} />
      </div>
      <Card label="Blended MER" value={formatRatio(k.blended_mer.value)} kpi={k.blended_mer} polarity="up_good"
            sub={`was ${formatRatio(k.blended_mer.previous)}`} />
      <Card label="Blended CAC" value={money(k.blended_cac.value, false)} kpi={k.blended_cac} polarity="down_good"
            sub={`${(k.new_customers.value ?? 0).toLocaleString("en-US")} new customers`} />
    </section>
  );
}
