"use client";

import { useMemo, useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Cell, ComposedChart, LabelList, Line, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

import { Skeleton } from "@/components/ui/skeleton";
import type { Daily, Overview } from "@/lib/api";
import { C } from "@/lib/colors";
import { formatDay, formatPct, niceTicks } from "@/lib/format";

import { useDisplay } from "./display-context";
import { Panel } from "./panel";
import { Segmented } from "./segmented";

type View = "waterfall" | "trend";

const TOOLTIP_BOX = "rounded-md border bg-card px-3 py-2 text-xs shadow-sm";

export function MarginChart({ overview, daily }: { overview: Overview | null; daily: Daily | null }) {
  const [view, setView] = useState<View>("waterfall");
  return (
    <Panel
      id="pnl"
      title="P&L and contribution margin"
      description={view === "waterfall"
        ? "Gross sales to net contribution (CM2) for the selected period"
        : "Daily net revenue, ad spend and CM2"}
      actions={
        <Segmented label="Chart view" size="sm" value={view} onChange={setView}
                   options={[{ value: "waterfall", label: "Breakdown" }, { value: "trend", label: "Daily trend" }]} />
      }
    >
      {view === "waterfall"
        ? (overview ? <Waterfall data={overview} /> : <Skeleton className="h-[340px] w-full" />)
        : (daily ? <Trend data={daily} /> : <Skeleton className="h-[340px] w-full" />)}
    </Panel>
  );
}

// ------------------------------------------------------------------ waterfall

function Waterfall({ data }: { data: Overview }) {
  const { toDisplay, money, axisMoney } = useDisplay();
  const gross = data.waterfall[0]?.value ?? 0;
  const rows = useMemo(() => {
    const out: (Overview["waterfall"][number] & { display: number; range: [number, number] })[] = [];
    let running = 0;
    for (const s of data.waterfall) {
      const v = toDisplay(s.value);
      const range: [number, number] = s.kind === "decrease" ? [running + v, running] : [Math.min(0, v), Math.max(0, v)];
      running = s.kind === "decrease" ? running + v : v;
      out.push({ ...s, display: v, range });
    }
    return out;
  }, [data.waterfall, toDisplay]);

  const wfTicks = useMemo(() => {
    const vals = rows.flatMap((r) => r.range);
    return niceTicks(Math.min(...vals), Math.max(...vals), 4);
  }, [rows]);

  const color = (kind: string, value: number) =>
    kind === "decrease" ? C.terracotta : kind === "subtotal" ? C.cobalt : value < 0 ? C.terracotta : C.ink;

  return (
    <div>
      <div className="h-[340px] w-full" role="img"
           aria-label={`Waterfall: ${rows.map((r) => `${r.step} ${money(r.value)}`).join(", ")}`}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 72, bottom: 4, left: 0 }} barCategoryGap={6}>
            <CartesianGrid horizontal={false} stroke={C.grid} />
            <XAxis type="number" ticks={wfTicks} domain={[wfTicks[0], wfTicks[wfTicks.length - 1]]} tickFormatter={axisMoney} tick={{ fill: C.muted, fontSize: 11 }}
                   axisLine={false} tickLine={false} />
            <YAxis type="category" dataKey="step" width={128}
                   tickFormatter={(s: string) => (s.startsWith("Net Contribution") ? "CM2 (net contrib.)" : s)} tick={{ fill: C.ink, fontSize: 12 }}
                   axisLine={false} tickLine={false} interval={0} />
            <ReferenceLine x={0} stroke={C.border} />
            <Tooltip cursor={{ fill: "rgba(17,18,20,0.04)" }}
                     content={({ active, payload }) => {
                       const r = active && payload?.[0]?.payload as (typeof rows)[number] | undefined;
                       if (!r) return null;
                       return (
                         <div className={TOOLTIP_BOX}>
                           <div className="font-medium">{r.step}</div>
                           <div className="num mt-0.5">{money(r.value, false)}</div>
                           <div className="text-muted-foreground num">{formatPct(gross ? Math.abs(r.value) / gross : null)} of gross sales</div>
                         </div>
                       );
                     }} />
            <Bar dataKey="range" maxBarSize={24} radius={4} isAnimationActive={false}>
              {rows.map((r) => <Cell key={r.step} fill={color(r.kind, r.display)} />)}
              <LabelList dataKey="display" position="right"
                         content={({ x, y, width, height, index }) => {
                           const r = rows[index as number];
                           const end = Number(x) + Math.max(Number(width), 0);
                           return (
                             <text x={end + 6} y={Number(y) + Number(height) / 2} dy={4} fontSize={11}
                                   fontFamily="var(--font-mono)" fill={r?.kind === "decrease" ? C.muted : C.ink}
                                   fontWeight={r?.kind === "decrease" ? 400 : 600}>
                               {r ? money(r.value) : null}
                             </text>
                           );
                         }} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <Legend items={[
        { color: C.ink, label: "Total", shape: "bar" },
        { color: C.cobalt, label: "Subtotal (Net revenue, CM1)", shape: "bar" },
        { color: C.terracotta, label: "Deduction", shape: "bar" },
      ]} />
    </div>
  );
}

// ------------------------------------------------------------------ daily trend

function Trend({ data }: { data: Daily }) {
  const { toDisplay, money, axisMoney } = useDisplay();
  const rows = useMemo(() => data.days.map((d) => ({
    date: d.date,
    net_revenue: toDisplay(d.net_revenue),
    ad_spend: toDisplay(d.ad_spend),
    cm2: toDisplay(d.cm2),
    raw: d,
  })), [data.days, toDisplay]);
  const ticks = useMemo(() => {
    const vals = rows.flatMap((r) => [r.net_revenue, r.ad_spend, r.cm2]);
    return niceTicks(Math.min(0, ...vals), Math.max(0, ...vals));
  }, [rows]);

  return (
    <div>
      <Legend items={[
        { color: C.cobalt, label: "Net revenue", shape: "bar" },
        { color: C.emerald, label: "CM2", shape: "line" },
        { color: C.amber, label: "Ad spend", shape: "dash" },
      ]} className="mb-2 mt-0" />
      <div className="h-[316px] w-full" role="img" aria-label={`Daily trend over ${rows.length} days`}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }} barCategoryGap={1}>
            <CartesianGrid vertical={false} stroke={C.grid} />
            <XAxis dataKey="date" tickFormatter={formatDay} tick={{ fill: C.muted, fontSize: 11 }}
                   axisLine={{ stroke: C.border }} tickLine={false} minTickGap={28} />
            <YAxis ticks={ticks} domain={[ticks[0], ticks[ticks.length - 1]]} tickFormatter={axisMoney}
                   tick={{ fill: C.muted, fontSize: 11 }} axisLine={false}
                   tickLine={false} width={56} />
            <ReferenceLine y={0} stroke={C.border} />
            <Tooltip cursor={{ stroke: C.muted, strokeWidth: 1 }}
                     content={({ active, payload }) => {
                       const r = active && payload?.[0]?.payload as (typeof rows)[number] | undefined;
                       if (!r) return null;
                       return (
                         <div className={TOOLTIP_BOX}>
                           <div className="font-medium">{formatDay(r.date)} · {r.raw.orders.toLocaleString("en-US")} orders</div>
                           <dl className="num mt-1 grid grid-cols-[auto_auto] gap-x-4 gap-y-0.5">
                             <dt className="text-muted-foreground">Net revenue</dt><dd className="text-right">{money(r.raw.net_revenue, false)}</dd>
                             <dt className="text-muted-foreground">Ad spend</dt><dd className="text-right">{money(r.raw.ad_spend, false)}</dd>
                             <dt className="text-muted-foreground">CM2</dt>
                             <dd className={r.raw.cm2 < 0 ? "text-risk text-right" : "text-right"}>{money(r.raw.cm2, false)}</dd>
                             <dt className="text-muted-foreground">MER</dt><dd className="text-right">{r.raw.mer?.toFixed(2) ?? "—"}x</dd>
                           </dl>
                         </div>
                       );
                     }} />
            <Bar dataKey="net_revenue" fill={C.cobalt} fillOpacity={0.85} maxBarSize={24} radius={[4, 4, 0, 0]}
                 isAnimationActive={false} />
            <Line dataKey="ad_spend" stroke={C.amber} strokeWidth={2} strokeDasharray="5 3" dot={false}
                  activeDot={{ r: 4, stroke: C.surface, strokeWidth: 2 }} isAnimationActive={false} />
            <Line dataKey="cm2" stroke={C.emerald} strokeWidth={2} dot={false}
                  activeDot={{ r: 4, stroke: C.surface, strokeWidth: 2 }} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function Legend({ items, className = "mt-2" }: {
  items: { color: string; label: string; shape: "bar" | "line" | "dash" }[]; className?: string;
}) {
  return (
    <ul className={`text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 text-xs ${className}`}>
      {items.map((i) => (
        <li key={i.label} className="flex items-center gap-1.5">
          <svg width="14" height="10" aria-hidden>
            {i.shape === "bar"
              ? <rect x="2" y="1" width="10" height="8" rx="2" fill={i.color} />
              : <line x1="0" y1="5" x2="14" y2="5" stroke={i.color} strokeWidth="2"
                      strokeDasharray={i.shape === "dash" ? "4 2" : undefined} />}
          </svg>
          {i.label}
        </li>
      ))}
    </ul>
  );
}
