"use client";

import { Check, ChevronsRight } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import type { CohortCell, CohortRow, Cohorts } from "@/lib/api";
import { formatInt, formatMonth, formatPct } from "@/lib/format";
import { cn } from "@/lib/utils";

import { useDisplay } from "./display-context";
import { Panel } from "./panel";
import { Segmented } from "./segmented";

type Mode = "retention" | "ltv";
type Basis = "contribution" | "net";

const mix = (hex: string, pct: number) => `color-mix(in oklab, ${hex} ${Math.round(pct)}%, #FFFFFF)`;

// Cobalt ramp text colour, computed for WCAG AA (11.5px figures need 4.5:1): ink (#111214)
// passes up to a 79% mix, white passes from 83%. The 80-82% band fails both, so it is
// snapped to 83%. Emerald (max 72%) and terracotta (max 58%) ramps always pass with ink.
function cobalt(pct: number) {
  const p = pct > 79 && pct < 83 ? 83 : pct;
  return { background: mix("#1D4ED8", p), dark: p >= 83 };
}

function cellStyle(cell: CohortCell, row: CohortRow, mode: Mode, basis: Basis, maxRetention: number, maxLtv: number) {
  if (!cell.available) return { background: "transparent", dark: false };
  if (mode === "retention") {
    if (cell.month === 0) return { background: "var(--muted)", dark: false };
    const t = Math.min((cell.retention ?? 0) / (maxRetention || 1), 1);
    return cobalt(6 + t * 82); // sequential: one hue, light -> dark
  }
  const ltv = basis === "contribution" ? cell.ltv_contribution : cell.ltv_net_revenue;
  if (ltv == null) return { background: "transparent", dark: false };
  if (basis === "net") {
    return cobalt(6 + Math.min(Math.max(ltv, 0) / (maxLtv || 1), 1) * 82);
  }
  // Diverging around CAC: below = terracotta, at CAC = neutral, above = emerald.
  if (!row.cac) return { background: "var(--muted)", dark: false };
  const ratio = ltv / row.cac;
  if (ratio >= 1) {
    const pct = 10 + Math.min(ratio - 1, 1) * 62;
    return { background: mix("#15803D", pct), dark: false };
  }
  const pct = 8 + Math.min(1 - ratio, 1) * 50;
  return { background: mix("#B91C1C", pct), dark: false };
}

export function CohortHeatmap({ data }: { data: Cohorts | null }) {
  const { money } = useDisplay();
  const [mode, setMode] = useState<Mode>("retention");
  const [basis, setBasis] = useState<Basis>("contribution");
  const [focus, setFocus] = useState<{ row: number; month: number } | null>(null);

  const { maxRetention, maxLtv } = useMemo(() => {
    let r = 0, l = 0;
    for (const row of data?.cohorts ?? []) for (const c of row.cells) {
      if (c.available && c.month > 0) r = Math.max(r, c.retention ?? 0);
      if (c.available) l = Math.max(l, c.ltv_net_revenue ?? 0);
    }
    return { maxRetention: r, maxLtv: l };
  }, [data]);

  const months = Array.from({ length: (data?.max_offset ?? 6) + 1 }, (_, i) => i);
  const focused = focus && data ? { row: data.cohorts[focus.row], cell: data.cohorts[focus.row]?.cells[focus.month] } : null;

  return (
    <Panel
      id="cohorts"
      title="Cohort retention & LTV payback"
      description="Customers grouped by first-order month. M0 is the acquisition month."
      actions={<>
        {mode === "ltv" && (
          <Segmented label="LTV basis" size="sm" value={basis} onChange={setBasis}
                     options={[{ value: "contribution", label: "Contribution", title: "Net revenue − COGS − shipping − fees" },
                               { value: "net", label: "Net revenue" }]} />
        )}
        <Segmented label="Heatmap metric" size="sm" value={mode} onChange={setMode}
                   options={[{ value: "retention", label: "Retention %" }, { value: "ltv", label: "Cumulative LTV" }]} />
      </>}
    >
      {!data ? <Skeleton className="h-[300px] w-full" /> : data.cohorts.length === 0 ? (
        <p className="text-muted-foreground py-10 text-center text-sm">No customers yet. Upload orders to build cohorts.</p>
      ) : (
        <>
          <SwipeScroller onMouseLeave={() => setFocus(null)}>
            <table className="w-full min-w-[720px] border-separate border-spacing-[2px] text-xs">
              <thead>
                <tr className="text-muted-foreground">
                  <th scope="col" className="bg-card sticky left-0 z-10 py-1.5 pr-2 text-left font-medium">Cohort</th>
                  <th scope="col" className="px-2 py-1.5 text-right font-medium">Customers</th>
                  <th scope="col" className="px-2 py-1.5 text-right font-medium">CAC</th>
                  {months.map((m) => (
                    <th key={m} scope="col"
                        className={cn("w-[9%] px-1 py-1.5 text-center font-medium", focus?.month === m && "text-foreground")}>
                      M{m}
                    </th>
                  ))}
                  <th scope="col" className="px-2 py-1.5 text-right font-medium">Payback</th>
                </tr>
              </thead>
              <tbody>
                {data.cohorts.map((row, ri) => (
                  <tr key={row.cohort_month}>
                    <th scope="row"
                        className={cn("bg-card sticky left-0 z-10 py-1 pr-2 text-left font-medium whitespace-nowrap",
                                      focus?.row === ri ? "text-foreground" : "text-foreground/80")}>
                      {formatMonth(row.cohort_month)}
                    </th>
                    <td className="num px-2 text-right">{formatInt(row.size)}</td>
                    <td className="num px-2 text-right">{money(row.cac, false)}</td>
                    {row.cells.map((cell) => {
                      const s = cellStyle(cell, row, mode, basis, maxRetention, maxLtv);
                      const ltv = basis === "contribution" ? cell.ltv_contribution : cell.ltv_net_revenue;
                      const paid = mode === "ltv" && basis === "contribution" && row.cac != null && ltv != null && ltv >= row.cac;
                      const label = !cell.available ? "" : mode === "retention" ? formatPct(cell.retention, cell.month === 0 ? 0 : 1) : money(ltv);
                      const active = focus?.row === ri && focus?.month === cell.month;
                      return (
                        <td key={cell.month} className="p-0">
                          {cell.available ? (
                            <button type="button"
                                    onMouseEnter={() => setFocus({ row: ri, month: cell.month })}
                                    onFocus={() => setFocus({ row: ri, month: cell.month })}
                                    aria-label={`${formatMonth(row.cohort_month)} cohort, M${cell.month}: ${label}${cell.partial ? " (month in progress)" : ""}`}
                                    style={{ background: s.background }}
                                    className={cn("num relative flex h-8 w-full items-center justify-center gap-0.5 rounded-[4px] text-[11.5px] outline-none",
                                                  s.dark ? "text-white" : "text-foreground",
                                                  active && "ring-foreground ring-2 ring-offset-1",
                                                  "focus-visible:ring-ring focus-visible:ring-2")}>
                              {paid && <Check className="size-3 shrink-0" aria-hidden strokeWidth={3} />}
                              {label}
                              {cell.partial && <span aria-hidden className="absolute top-0.5 right-1 text-[9px] opacity-70">•</span>}
                            </button>
                          ) : (
                            <div className="h-8 rounded-[4px] bg-[repeating-linear-gradient(135deg,#F4F5F7_0_4px,transparent_4px_8px)]" aria-hidden />
                          )}
                        </td>
                      );
                    })}
                    <td className="num px-2 text-right whitespace-nowrap">
                      {row.payback_month != null
                        ? <span className="text-positive font-medium">M{row.payback_month}</span>
                        : <span className="text-muted-foreground">not yet</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </SwipeScroller>

          <div className="bg-muted/60 mt-3 min-h-[44px] rounded-md px-3 py-2 text-xs" aria-live="polite">
            {focused?.cell ? <CellDetail row={focused.row} cell={focused.cell} /> : (
              <span className="text-muted-foreground">
                Hover or tab through a cell for detail. • marks the current month (still filling in). Payback = first month
                cumulative contribution per customer covers CAC{mode === "ltv" && basis === "contribution" ? " (✓)" : ""}.
              </span>
            )}
          </div>
          <HeatLegend mode={mode} basis={basis} />
        </>
      )}
    </Panel>
  );
}

function CellDetail({ row, cell }: { row: CohortRow; cell: CohortCell }) {
  const { money } = useDisplay();
  const recovered = row.cac && cell.ltv_contribution != null ? cell.ltv_contribution / row.cac : null;
  return (
    <div className="num flex flex-wrap gap-x-4 gap-y-1">
      <span className="font-sans font-semibold">{formatMonth(row.cohort_month)} cohort · M{cell.month}{cell.partial ? " (in progress)" : ""}</span>
      <span>{formatInt(cell.active)} of {formatInt(row.size)} active ({formatPct(cell.retention)})</span>
      <span>LTV net {money(cell.ltv_net_revenue, false)}</span>
      <span>LTV contribution {money(cell.ltv_contribution, false)}</span>
      <span className={recovered != null && recovered >= 1 ? "text-positive" : "text-risk"}>
        {formatPct(recovered, 0)} of CAC {money(row.cac, false)} recovered
      </span>
    </div>
  );
}

function HeatLegend({ mode, basis }: { mode: Mode; basis: Basis }) {
  const diverging = mode === "ltv" && basis === "contribution";
  return (
    <div className="text-muted-foreground mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]">
      {diverging ? (
        <>
          <span>Below CAC</span>
          <span className="flex h-2.5 w-28 overflow-hidden rounded-sm" aria-hidden>
            {[58, 32, 10].map((p) => <span key={p} className="flex-1" style={{ background: mix("#B91C1C", p) }} />)}
            <span className="bg-muted flex-1" />
            {[20, 45, 72].map((p) => <span key={p} className="flex-1" style={{ background: mix("#15803D", p) }} />)}
          </span>
          <span>Above CAC ✓</span>
        </>
      ) : (
        <>
          <span>Low</span>
          <span className="h-2.5 w-28 rounded-sm" aria-hidden
                style={{ background: `linear-gradient(90deg, ${mix("#1D4ED8", 6)}, ${mix("#1D4ED8", 88)})` }} />
          <span>High {mode === "retention" ? "repeat rate (M1+)" : "net revenue per customer"}</span>
        </>
      )}
    </div>
  );
}

/**
 * Horizontal scroller with a "Swipe M0 → M6" cue and edge fades. The cue only
 * renders when the table really overflows (phones, narrow tablets) and fades out
 * once the user reaches the last column.
 */
function SwipeScroller({ children, onMouseLeave }: { children: React.ReactNode; onMouseLeave?: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const [edge, setEdge] = useState({ overflow: false, start: true, end: true });
  const measure = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const overflow = el.scrollWidth - el.clientWidth > 4;
    const next = { overflow, start: el.scrollLeft <= 4, end: el.scrollLeft + el.clientWidth >= el.scrollWidth - 4 };
    setEdge((e) => (e.overflow === next.overflow && e.start === next.start && e.end === next.end ? e : next));
  }, []);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    if (el.firstElementChild) ro.observe(el.firstElementChild);
    return () => ro.disconnect();
  }, [measure]);

  return (
    <div>
      {edge.overflow && (
        <div aria-hidden data-swipe-cue
             className={cn("text-muted-foreground mb-1.5 flex items-center justify-end gap-1 text-[11px] transition-opacity duration-300",
                           edge.end && "opacity-0")}>
          Swipe M0 → M6 <ChevronsRight className="size-3.5 animate-[nudge_1.6s_ease-in-out_infinite] motion-reduce:animate-none" />
        </div>
      )}
      <div className="relative -mx-4">
        <div ref={ref} onScroll={measure} onMouseLeave={onMouseLeave} className="max-md:no-scrollbar overflow-x-auto px-4">
          {children}
        </div>
        <div aria-hidden className={cn("from-card pointer-events-none absolute inset-y-0 left-0 w-6 bg-gradient-to-r to-transparent transition-opacity",
                                       (!edge.overflow || edge.start) && "opacity-0")} />
        <div aria-hidden className={cn("from-card pointer-events-none absolute inset-y-0 right-0 w-10 bg-gradient-to-l to-transparent transition-opacity",
                                       (!edge.overflow || edge.end) && "opacity-0")} />
      </div>
    </div>
  );
}
