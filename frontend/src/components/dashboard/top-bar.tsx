"use client";

import { Activity, MessageSquareText, Upload } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { RANGES, type DemoStatus, type RangePreset, type Tenant } from "@/lib/api";
import type { Currency } from "@/lib/format";

import { ResetDemoButton, SandboxBadge } from "./demo-controls";
import { Segmented } from "./segmented";

export function TopBar({ tenants, tenantId, onTenant, currency, onCurrency, range, onRange, onUpload, onAnalyst, demo, onResetDemo }: {
  tenants: Tenant[]; tenantId: string | null; onTenant: (id: string) => void;
  currency: Currency; onCurrency: (c: Currency) => void;
  range: RangePreset; onRange: (r: RangePreset) => void;
  onUpload: () => void; onAnalyst: () => void;
  demo: DemoStatus | null; onResetDemo: () => Promise<void>;
}) {
  const sandbox = demo?.demo_mode ? demo : null;
  const items = tenants.map((t) => ({ value: t.id, label: t.name }));
  return (
    // Pinned at top-0 on every breakpoint. Below md it's two fixed-height rows (brand + actions,
    // then one non-wrapping filter row that scrolls sideways) so it never grows past ~92px.
    // Single row from lg up.
    <header className="bg-card/95 sticky top-0 z-30 border-b backdrop-blur supports-[backdrop-filter]:bg-card/85">
      <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2 md:px-6 lg:flex-nowrap lg:py-2.5">
        <div className="mr-auto flex items-center gap-2">
          <span className="bg-foreground text-background grid size-7 place-items-center rounded-md" aria-hidden>
            <Activity className="size-4" />
          </span>
          <span className="text-[15px] font-semibold tracking-tight">Ecom Pulse</span>
          {sandbox && <SandboxBadge status={sandbox} onReset={onResetDemo} />}
        </div>

        <div className="no-scrollbar order-last -mx-4 flex w-[calc(100%+2rem)] items-center gap-2 overflow-x-auto px-4 md:-mx-6 md:w-[calc(100%+3rem)] md:px-6 lg:order-none lg:mx-0 lg:w-auto lg:overflow-visible lg:px-0">
          <Select items={items} value={tenantId} onValueChange={(v) => v && onTenant(v as string)}>
            <SelectTrigger aria-label="Tenant" className="bg-card h-8 w-[168px] shrink-0 lg:w-auto lg:min-w-[190px]">
              <SelectValue placeholder="Select tenant" />
            </SelectTrigger>
            <SelectContent>
              {tenants.map((t) => (
                <SelectItem key={t.id} value={t.id}>
                  {t.name} <span className="text-muted-foreground ml-1 font-mono text-[11px]">{t.currency}</span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Segmented label="Display currency" value={currency} onChange={onCurrency} className="shrink-0"
                     options={[{ value: "INR", label: "₹ INR" }, { value: "USD", label: "$ USD" }]} />
          <Segmented label="Date range" value={range} onChange={onRange} className="shrink-0"
                     options={RANGES.map((r) => ({ value: r, label: r, title: r === "QTD" ? "Quarter to date" : `Last ${r.slice(0, -1)} days` }))} />
        </div>

        <div className="flex items-center gap-2">
          {sandbox && <ResetDemoButton status={sandbox} onReset={onResetDemo} className="max-sm:hidden" />}
          <Button variant="outline" size="sm" onClick={onUpload} aria-label="Upload CSV">
            <Upload className="size-3.5" aria-hidden /><span className={sandbox ? "max-sm:hidden" : undefined}>Upload CSV</span>
          </Button>
          <Button size="sm" onClick={onAnalyst}>
            <MessageSquareText className="size-3.5" aria-hidden /><span>Analyst</span>
          </Button>
        </div>
      </div>
    </header>
  );
}
