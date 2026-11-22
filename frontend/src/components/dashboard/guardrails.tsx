"use client";

import { ArrowDown, ArrowUp, ArrowUpDown, Flame, ShieldAlert } from "lucide-react";
import { useMemo, useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import type { Channels, Segment, SkuRow, Skus } from "@/lib/api";
import { formatInt, formatPct, formatRatio } from "@/lib/format";
import { cn } from "@/lib/utils";

import { useDisplay } from "./display-context";
import { Panel } from "./panel";
import { Segmented } from "./segmented";

type Tab = "skus" | "channels";
type Filter = "all" | Segment;
type SortKey = "name" | "net_revenue" | "ad_spend" | "return_rate" | "repeat_rate" | "cm2" | "cm2_pct" | "true_roas";

const SEGMENT_META: Record<Segment, { label: string; className: string; bar: string; icon?: typeof Flame }> = {
  hero: { label: "Hero", className: "bg-positive/8 text-positive", bar: "bg-positive", icon: Flame },
  margin_leak: { label: "Margin leak", className: "bg-risk/10 text-risk", bar: "bg-risk", icon: ShieldAlert },
  core: { label: "Core", className: "bg-muted text-muted-foreground", bar: "bg-transparent" },
};

export function Guardrails({ skus, channels }: { skus: Skus | null; channels: Channels | null }) {
  const [tab, setTab] = useState<Tab>("skus");
  return (
    <Panel
      id="guardrails"
      title="SKU & channel guardrails"
      description={tab === "skus"
        ? "Hero SKUs earn repeat purchases at healthy CM2. Margin leaks burn ad spend or lose sales to refunds/RTO."
        : "True ROAS uses net revenue after discounts, refunds and RTO. Platform ROAS is what the ad platform claims."}
      actions={<Segmented label="Guardrail table" size="sm" value={tab} onChange={setTab}
                          options={[{ value: "skus", label: "SKUs" }, { value: "channels", label: "Channels" }]} />}
    >
      {tab === "skus"
        ? (skus ? <SkuTable data={skus} /> : <Skeleton className="h-[420px] w-full" />)
        : (channels ? <ChannelTable data={channels} /> : <Skeleton className="h-[200px] w-full" />)}
    </Panel>
  );
}

function SortHeader({ label, k, sort, onSort, align = "right" }: {
  label: string; k: SortKey; sort: { key: SortKey; dir: 1 | -1 }; onSort: (k: SortKey) => void; align?: "left" | "right";
}) {
  const active = sort.key === k;
  const Icon = !active ? ArrowUpDown : sort.dir === 1 ? ArrowUp : ArrowDown;
  return (
    <th scope="col" aria-sort={active ? (sort.dir === 1 ? "ascending" : "descending") : "none"}
        className={cn("px-2 py-2 font-medium whitespace-nowrap", align === "right" ? "text-right" : "text-left")}>
      <button type="button" onClick={() => onSort(k)}
              className={cn("inline-flex items-center gap-1 rounded outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring",
                            active && "text-foreground")}>
        {label}<Icon className={cn("size-3", !active && "opacity-40")} aria-hidden />
      </button>
    </th>
  );
}

function SkuTable({ data }: { data: Skus }) {
  const { money } = useDisplay();
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "cm2", dir: -1 });
  const counts = useMemo(() => {
    const c: Record<Filter, number> = { all: data.skus.length, hero: 0, margin_leak: 0, core: 0 };
    for (const s of data.skus) c[s.segment] += 1;
    return c;
  }, [data.skus]);
  const rows = useMemo(() => {
    const list = data.skus.filter((s) => filter === "all" || s.segment === filter);
    return [...list].sort((a, b) => {
      const av = a[sort.key], bv = b[sort.key];
      if (typeof av === "string" || typeof bv === "string") return String(av).localeCompare(String(bv)) * sort.dir;
      return (((av as number | null) ?? -Infinity) - ((bv as number | null) ?? -Infinity)) * sort.dir;
    });
  }, [data.skus, filter, sort]);
  const onSort = (key: SortKey) =>
    setSort((s) => (s.key === key ? { key, dir: (s.dir * -1) as 1 | -1 } : { key, dir: key === "name" ? 1 : -1 }));
  const t = data.thresholds;

  return (
    <div>
      <Segmented label="Filter SKUs by segment" size="sm" value={filter} onChange={setFilter} className="mb-3"
                 options={[
                   { value: "all", label: `All ${counts.all}` },
                   { value: "hero", label: `Hero ${counts.hero}` },
                   { value: "margin_leak", label: `Margin leak ${counts.margin_leak}` },
                   { value: "core", label: `Core ${counts.core}` },
                 ]} />
      <div className="-mx-4 overflow-x-auto">
        <table className="w-full min-w-[860px] text-xs">
          <thead className="text-muted-foreground border-y bg-background">
            <tr>
              <SortHeader label="SKU" k="name" sort={sort} onSort={onSort} align="left" />
              <th scope="col" className="px-2 py-2 text-left font-medium">Segment</th>
              <SortHeader label="Net revenue" k="net_revenue" sort={sort} onSort={onSort} />
              <SortHeader label="Ad spend" k="ad_spend" sort={sort} onSort={onSort} />
              <SortHeader label="Refund/RTO" k="return_rate" sort={sort} onSort={onSort} />
              <SortHeader label="Repeat rate" k="repeat_rate" sort={sort} onSort={onSort} />
              <SortHeader label="True ROAS" k="true_roas" sort={sort} onSort={onSort} />
              <SortHeader label="CM2" k="cm2" sort={sort} onSort={onSort} />
              <SortHeader label="CM2 %" k="cm2_pct" sort={sort} onSort={onSort} />
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => <SkuTr key={s.sku} s={s} money={money} t={t} />)}
            {rows.length === 0 && (
              <tr><td colSpan={9} className="text-muted-foreground py-8 text-center">No SKUs in this segment.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <p className="text-muted-foreground mt-3 text-[11px] leading-relaxed">
        <b className="text-foreground font-medium">Hero</b>: repeat rate ≥ {formatPct(t.hero_min_repeat_rate, 0)} and CM2 ≥{" "}
        {formatPct(t.hero_min_cm2_pct, 0)} of net revenue. <b className="text-foreground font-medium">Margin leak</b>: negative CM2,
        or refund/RTO ≥ {formatPct(t.leak_min_return_rate, 0)} with ad spend ≥ {formatPct(t.leak_min_ad_to_revenue, 0)} of net revenue.
        Unmapped ad spend is allocated by net revenue share, so SKU CM2 adds up to total CM2.
      </p>
    </div>
  );
}

function SkuTr({ s, money, t }: { s: SkuRow; money: (v: number | null) => string; t: Skus["thresholds"] }) {
  const meta = SEGMENT_META[s.segment];
  const Icon = meta.icon;
  const highReturn = (s.return_rate ?? 0) >= t.leak_min_return_rate;
  return (
    <tr className="border-b last:border-b-0 hover:bg-muted/40">
      <td className="relative py-2 pr-2 pl-4">
        <span className={cn("absolute inset-y-1 left-0 w-[3px] rounded-r", meta.bar)} aria-hidden />
        <div className="max-w-[240px] truncate font-medium" title={s.name}>{s.name}</div>
        <div className="text-muted-foreground font-mono text-[10.5px]">{s.sku}</div>
      </td>
      <td className="px-2 py-2">
        <span className={cn("inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium whitespace-nowrap", meta.className)}
              title={s.reasons.join("; ") || undefined}>
          {Icon && <Icon className="size-3" aria-hidden />}{meta.label}
        </span>
        {s.reasons.length > 0 && s.segment === "margin_leak" && (
          <div className="text-muted-foreground mt-0.5 max-w-[180px] text-[10.5px] leading-tight">{s.reasons.join(" · ")}</div>
        )}
      </td>
      <td className="num px-2 py-2 text-right">{money(s.net_revenue)}</td>
      <td className="num px-2 py-2 text-right">
        {money(s.ad_spend)}
        <div className="text-muted-foreground text-[10.5px]">{formatPct(s.ad_to_revenue, 0)} of net</div>
      </td>
      <td className={cn("num px-2 py-2 text-right", highReturn && "text-risk font-medium")}>{formatPct(s.return_rate)}</td>
      <td className={cn("num px-2 py-2 text-right", (s.repeat_rate ?? 0) >= t.hero_min_repeat_rate && "text-positive font-medium")}>
        {formatPct(s.repeat_rate)}
      </td>
      <td className="num px-2 py-2 text-right">{formatRatio(s.true_roas)}</td>
      <td className={cn("num px-2 py-2 text-right font-semibold", s.cm2 < 0 && "text-risk")}>{money(s.cm2)}</td>
      <td className={cn("num px-2 py-2 pr-4 text-right", (s.cm2_pct ?? 0) < 0 && "text-risk")}>{formatPct(s.cm2_pct)}</td>
    </tr>
  );
}

function ChannelTable({ data }: { data: Channels }) {
  const { money } = useDisplay();
  const paid = data.channels.filter((c) => c.ad_spend > 0);
  const organic = data.channels.filter((c) => c.ad_spend === 0);
  const best = paid.reduce<string | null>((b, c) =>
    b == null || (c.true_roas ?? 0) > (paid.find((p) => p.channel === b)?.true_roas ?? 0) ? c.channel : b, null);
  const name = (c: string) => c.charAt(0).toUpperCase() + c.slice(1);
  return (
    <div className="-mx-4 overflow-x-auto">
      <table className="w-full min-w-[820px] text-xs">
        <thead className="text-muted-foreground border-y bg-background">
          <tr>
            {["Channel", "Ad spend", "Platform ROAS", "True ROAS", "ROAS inflation", "Refunds/RTO", "Net revenue", "New customers", "CAC"]
              .map((h, i) => <th key={h} scope="col" className={cn("px-2 py-2 font-medium whitespace-nowrap", i === 0 ? "pl-4 text-left" : "text-right", i === 8 && "pr-4")}>{h}</th>)}
          </tr>
        </thead>
        <tbody>
          {[...paid, ...organic].map((c) => (
            <tr key={c.channel} className="border-b last:border-b-0 hover:bg-muted/40">
              <td className="py-2.5 pr-2 pl-4 font-medium">
                {name(c.channel)}
                {c.channel === best && paid.length > 1 && (
                  <span className="bg-positive/8 text-positive ml-2 rounded px-1.5 py-0.5 text-[10.5px] font-medium">Best True ROAS</span>
                )}
              </td>
              <td className="num px-2 text-right">{c.ad_spend ? money(c.ad_spend) : "—"}</td>
              <td className="num text-muted-foreground px-2 text-right">{formatRatio(c.platform_roas)}</td>
              <td className="num px-2 text-right font-semibold">{formatRatio(c.true_roas)}</td>
              <td className={cn("num px-2 text-right", (c.roas_inflation ?? 0) >= 1.5 && "text-risk font-medium")}>
                {c.roas_inflation ? `${formatRatio(c.roas_inflation)} overstated` : "—"}
              </td>
              <td className="num px-2 text-right">{money(c.refunds_rto)}</td>
              <td className="num px-2 text-right">{money(c.attributed_net_revenue)}</td>
              <td className="num px-2 text-right">{formatInt(c.new_customers)}</td>
              <td className="num px-2 pr-4 text-right">{c.cac == null ? "—" : money(c.cac)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
