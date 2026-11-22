"use client";

import { ArrowRight, Database, Loader2, MessageSquareText, ShieldCheck, TriangleAlert } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { api, type AnalystAnswer, type AnalystPrompt, type CellFormat, type RangePreset, type Tenant } from "@/lib/api";
import type { Currency } from "@/lib/format";
import { formatInt, formatMonth, formatPct, formatRatio } from "@/lib/format";
import { cn } from "@/lib/utils";

import { useDisplay } from "./display-context";

interface Turn { id: number; question: string; answer?: AnalystAnswer; error?: string }

export function AnalystDrawer({ open, onOpenChange, tenant, range, displayCurrency, dataVersion }: {
  open: boolean; onOpenChange: (o: boolean) => void; tenant: Tenant | null; range: RangePreset;
  displayCurrency: Currency; dataVersion: number;
}) {
  const [prompts, setPrompts] = useState<AnalystPrompt[]>([]);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const seq = useRef(0);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => { api.prompts().then(setPrompts, () => setPrompts([])); }, []);
  // A different tenant, range or dataset makes earlier answers stale.
  useEffect(() => { setTurns([]); }, [tenant?.id, range, dataVersion, displayCurrency]);
  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    bottom.current?.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "end" });
  }, [turns]);

  async function ask(body: { question?: string; prompt_id?: string }, label: string) {
    if (!tenant || busy) return;
    const id = ++seq.current;
    setTurns((t) => [...t, { id, question: label }]);
    setBusy(true);
    try {
      const answer = await api.ask(tenant.id, { ...body, range, display_currency: displayCurrency });
      setTurns((t) => t.map((x) => (x.id === id ? { ...x, answer } : x)));
    } catch (e) {
      setTurns((t) => t.map((x) => (x.id === id ? { ...x, error: (e as Error).message } : x)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full gap-0 data-[side=right]:w-full data-[side=right]:sm:max-w-xl">
        <SheetHeader className="border-b">
          <SheetTitle className="flex items-center gap-2"><MessageSquareText className="text-primary size-4" aria-hidden />Analyst</SheetTitle>
          <SheetDescription className="flex items-start gap-1.5">
            <ShieldCheck className="text-positive mt-0.5 size-3.5 shrink-0" aria-hidden />
            Read-only. Answers come from the same pre-computed metrics as the dashboard ({range}); it never
            runs ad-hoc SQL or changes data.
          </SheetDescription>
        </SheetHeader>

        <div className="flex min-h-0 flex-1 flex-col">
          <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
            {turns.length === 0 && (
              <div>
                <div className="text-muted-foreground mb-2 text-xs font-medium tracking-wide uppercase">Suggested questions</div>
                <div className="grid gap-2">
                  {prompts.map((p) => (
                    <button key={p.id} type="button" onClick={() => ask({ prompt_id: p.id }, p.question)} disabled={busy || !tenant}
                            className="group hover:border-primary/40 hover:bg-accent flex items-center justify-between gap-3 rounded-lg border px-3 py-2.5 text-left text-sm transition-colors disabled:opacity-50">
                      {p.question}
                      <ArrowRight className="text-muted-foreground group-hover:text-primary size-4 shrink-0" aria-hidden />
                    </button>
                  ))}
                </div>
              </div>
            )}
            {turns.map((t) => (
              <div key={t.id} className="space-y-2">
                <div className="bg-foreground text-background ml-auto w-fit max-w-[85%] rounded-lg rounded-br-sm px-3 py-2 text-sm">
                  {t.question}
                </div>
                {!t.answer && !t.error && (
                  <div className="text-muted-foreground flex items-center gap-2 text-sm">
                    <Loader2 className="size-4 animate-spin" aria-hidden />Reading dashboard metrics…
                  </div>
                )}
                {t.error && <div role="alert" className="text-risk text-sm">Request failed: {t.error}</div>}
                {t.answer && <AnswerCard a={t.answer} onSuggestion={(p) => ask({ prompt_id: p.id }, p.question)} />}
              </div>
            ))}
            <div ref={bottom} />
          </div>

          {turns.length > 0 && (
            <div className="flex gap-1.5 overflow-x-auto border-t px-4 pt-3 pb-1">
              {prompts.map((p) => (
                <button key={p.id} type="button" onClick={() => ask({ prompt_id: p.id }, p.question)} disabled={busy}
                        className="hover:bg-accent shrink-0 rounded-full border px-2.5 py-1 text-xs whitespace-nowrap disabled:opacity-50">
                  {p.question}
                </button>
              ))}
            </div>
          )}
          <form className="flex gap-2 p-4 pt-3" onSubmit={(e) => {
            e.preventDefault();
            const q = text.trim();
            if (q) { ask({ question: q }, q); setText(""); }
          }}>
            <input value={text} onChange={(e) => setText(e.target.value)} maxLength={500}
                   placeholder="Ask about margin leaks, cohorts, ROAS, COD/RTO…" aria-label="Ask the analyst"
                   className="border-input focus-visible:border-ring focus-visible:ring-ring/50 h-9 min-w-0 flex-1 rounded-lg border bg-transparent px-3 text-base outline-none focus-visible:ring-2 sm:text-sm" />
            <Button type="submit" disabled={!text.trim() || busy || !tenant}>Ask</Button>
          </form>
        </div>
      </SheetContent>
    </Sheet>
  );
}

function AnswerCard({ a, onSuggestion }: { a: AnalystAnswer; onSuggestion: (p: AnalystPrompt) => void }) {
  const { money } = useDisplay();
  const fmt = (v: unknown, f: CellFormat): string => {
    if (v == null) return "—";
    switch (f) {
      case "money": return money(v as number);
      case "pct": return formatPct(v as number);
      case "ratio": return formatRatio(v as number);
      case "int": return formatInt(v as number);
      case "month": return formatMonth(String(v));
      case "month_offset": return `M${v}`;
      default: return String(v).replace(/_/g, " ");
    }
  };
  const answered = a.status === "answered";
  return (
    <article className={cn("rounded-lg border p-3", !answered && "border-amber-700/30 bg-amber-50/40")}>
      <h3 className="flex items-center gap-1.5 text-sm font-semibold">
        {!answered && <TriangleAlert className="size-4 text-amber-700" aria-hidden />}{a.title}
      </h3>
      <div className="mt-1.5 space-y-1.5 text-[13px] leading-relaxed">
        {a.summary.map((s, i) => <p key={i}>{s}</p>)}
      </div>

      {a.highlights && a.highlights.length > 0 && (
        <dl className="mt-3 grid grid-cols-[repeat(auto-fit,minmax(120px,1fr))] gap-px overflow-hidden rounded-md border bg-border">
          {a.highlights.map((h) => (
            <div key={h.label} className="bg-card p-2">
              <dt className="text-muted-foreground text-[11px]">{h.label}</dt>
              <dd className={cn("num text-sm font-semibold", h.tone === "risk" && "text-risk", h.tone === "positive" && "text-positive")}>
                {fmt(h.value, h.format)}
              </dd>
            </div>
          ))}
        </dl>
      )}

      {a.table && a.table.rows.length > 0 && <AnswerTable table={a.table} fmt={fmt} />}

      {a.actions && a.actions.length > 0 && (
        <ul className="mt-3 space-y-1 text-[12.5px]">
          {a.actions.map((x, i) => (
            <li key={i} className="flex gap-1.5"><ArrowRight className="text-primary mt-0.5 size-3.5 shrink-0" aria-hidden />{x}</li>
          ))}
        </ul>
      )}

      {a.suggestions && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {a.suggestions.map((p) => (
            <button key={p.id} type="button" onClick={() => onSuggestion(p)}
                    className="hover:bg-accent rounded-full border bg-card px-2.5 py-1 text-xs">{p.question}</button>
          ))}
        </div>
      )}

      <footer className="text-muted-foreground mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 border-t pt-2 text-[11px]">
        <span className="flex items-center gap-1"><ShieldCheck className="size-3" aria-hidden />{answered ? "Read-only answer" : a.status === "refused" ? "Blocked by guardrail" : "Not supported"}</span>
        {a.sources?.map((s) => (
          <span key={s} className="flex items-center gap-1 font-mono"><Database className="size-3" aria-hidden />{s}</span>
        ))}
        <span className="num">{a.period.start} → {a.period.end}</span>
      </footer>
    </article>
  );
}

type AnswerTableData = NonNullable<AnalystAnswer["table"]>;

// Column sizing is computed, not guessed: each numeric column must fit its longest header
// word (headers may wrap between words, not inside one) and its longest value. If the
// columns don't all fit at that size, rows are stacked as label/value cards instead.
const SANS_PX = 6.3; // ~average glyph width of 11px Plus Jakarta Sans (medium)
const MONO_PX = 6.7; // 11px JetBrains Mono
const CELL_PAD = 14; // px-1.5 on both sides + a little slack
const FIRST_COL_MIN = 120;

/** Fits the drawer at any width: a fixed-layout table when there's room, stacked cards when there isn't. */
function AnswerTable({ table, fmt }: { table: AnswerTableData; fmt: (v: unknown, f: CellFormat) => string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState<number | null>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(e.contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const [first, ...rest] = table.columns;
  const mins = rest.map((c) => {
    const word = Math.max(...c.label.split(/\s+/).map((w) => w.length));
    const value = Math.max(0, ...table.rows.map((r) => fmt(r[c.key], c.format).length));
    return Math.ceil(Math.max(word * SANS_PX, value * MONO_PX) + CELL_PAD);
  });
  const needed = FIRST_COL_MIN + mins.reduce((a, b) => a + b, 0);
  const asTable = width != null && width >= needed;
  // Spare room goes to the first (name) column; numeric columns keep their measured minimum.
  const colWidths = width ? mins.map((m) => `${m}px`) : [];
  return (
    <div ref={ref} className="mt-3" data-answer-table={asTable ? "table" : "cards"}>
      {asTable ? (
        <div className="overflow-hidden rounded-md border">
          <table className="w-full table-fixed text-[11px]">
            <colgroup>
              <col />
              {rest.map((c, i) => <col key={c.key} style={{ width: colWidths[i] }} />)}
            </colgroup>
            <thead className="text-muted-foreground bg-background">
              <tr>
                {table.columns.map((c, i) => (
                  <th key={c.key} scope="col"
                      className={cn("px-1.5 py-1.5 align-bottom leading-tight font-medium break-normal", i === 0 ? "pl-2 text-left" : "text-right", i === table.columns.length - 1 && "pr-2")}>
                    {c.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {table.rows.map((r, ri) => (
                <tr key={ri} className="border-t">
                  {table.columns.map((c, i) => (
                    <td key={c.key} title={i === 0 ? fmt(r[c.key], c.format) : undefined}
                        className={cn("px-1.5 py-1.5 whitespace-nowrap", i === 0 ? "truncate pl-2 text-left" : "num text-right",
                                      i === table.columns.length - 1 && "pr-2")}>
                      {fmt(r[c.key], c.format)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <ul className="divide-y overflow-hidden rounded-md border">
          {table.rows.map((r, ri) => (
            <li key={ri} className="px-2.5 py-2">
              <div className="truncate text-[12px] font-medium">{fmt(r[first.key], first.format)}</div>
              <dl className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 text-[11px]">
                {rest.map((c) => (
                  <div key={c.key} className="flex min-w-0 justify-between gap-2">
                    <dt className="text-muted-foreground truncate">{c.label}</dt>
                    <dd className="num shrink-0">{fmt(r[c.key], c.format)}</dd>
                  </div>
                ))}
              </dl>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
