"use client";

import { CheckCircle2, Copy, Loader2, RefreshCw, TriangleAlert } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { useApi } from "@/hooks/use-api";
import { api, API_BASE, ApiError, type DemoStatus, type Provider, type Tenant } from "@/lib/api";
import { cn } from "@/lib/utils";

const BLURB: Record<Provider, string> = {
  shopify: "Orders, line items, refunds and RTO status.",
  meta: "Daily spend, impressions, clicks and platform-claimed revenue.",
  google: "Daily spend, clicks and platform-claimed revenue.",
  webhook: "Push orders from any system with one POST request.",
};

type Outcome = { ok: true; text: string } | { ok: false; text: string };

function ago(iso: string | null): string {
  if (!iso) return "Never synced";
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return "Synced just now";
  if (s < 3600) return `Synced ${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `Synced ${Math.floor(s / 3600)} h ago`;
  return `Synced ${Math.floor(s / 86400)} d ago`;
}

export function ConnectDrawer({ open, onOpenChange, tenant, demo, onSynced }: {
  open: boolean; onOpenChange: (o: boolean) => void; tenant: Tenant | null; demo: DemoStatus | null; onSynced: () => void;
}) {
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState<Provider | null>(null);
  const [outcome, setOutcome] = useState<Record<string, Outcome>>({});
  const tid = tenant?.id ?? null;
  const conns = useApi(open && tid ? () => api.connections(tid) : null, `conn|${tid}|${open}|${reload}`);
  const items = conns.data?.connections ?? null;
  const loadError = conns.error?.message ?? null;

  async function sync(p: Provider) {
    if (!tid || busy) return;
    setBusy(p);
    setOutcome((o) => Object.fromEntries(Object.entries(o).filter(([k]) => k !== p)));
    try {
      const r = await api.sync(tid, p);
      setOutcome((o) => ({ ...o, [p]: { ok: true, text: r.summary } }));
      onSynced();
      setReload((n) => n + 1);
    } catch (e) {
      const err = e as ApiError;
      setOutcome((o) => ({ ...o, [p]: { ok: false, text: err.message } }));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full gap-0 data-[side=right]:w-full overflow-y-auto data-[side=right]:sm:max-w-md">
        <SheetHeader className="border-b">
          <SheetTitle>Connect APIs</SheetTitle>
          <SheetDescription>
            Into <b className="text-foreground">{tenant?.name ?? "…"}</b>. The Local Simulator writes sample orders and ad spend
            into your local database, then every metric recomputes. Nothing leaves your machine.
            {demo && (
              <span className="border-positive/25 bg-positive/5 text-foreground mt-2 block rounded-md border px-2.5 py-1.5">
                Live Sandbox: syncs go into a private copy for your browser.
              </span>
            )}
          </SheetDescription>
        </SheetHeader>

        {loadError && (
          <div role="alert" className="border-risk/30 bg-risk/5 text-risk m-4 flex gap-2 rounded-md border p-3 text-sm">
            <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />Couldn&apos;t load connections: {loadError}
          </div>
        )}
        {!items && !loadError && (
          <div className="text-muted-foreground flex items-center gap-2 p-4 text-sm"><Loader2 className="size-4 animate-spin" aria-hidden />Loading…</div>
        )}

        <ul className="divide-y">
          {items?.map((c) => (
            <li key={c.provider} data-testid={`connection-${c.provider}`} className="flex flex-col gap-3 p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h3 className="text-sm font-semibold">{c.label}</h3>
                  <p className="text-muted-foreground mt-0.5 text-xs">{BLURB[c.provider]}</p>
                </div>
                <span className={cn("shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-medium",
                                    c.connected ? "border-positive/25 text-positive" : "text-muted-foreground")}>
                  {c.connected ? "Simulator active" : "Not synced"}
                </span>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <div role="radiogroup" aria-label={`${c.label} source`} className="bg-card inline-flex rounded-lg border p-0.5">
                  <button type="button" role="radio" aria-checked
                          className="bg-foreground text-background h-7 rounded-md px-2.5 text-[13px] font-medium">Local Simulator</button>
                  <button type="button" role="radio" aria-checked={false} disabled
                          title="Live APIs need approved app credentials. Not available locally."
                          className="text-muted-foreground h-7 cursor-not-allowed rounded-md px-2.5 text-[13px] font-medium opacity-60">Live API</button>
                </div>
                <Button size="sm" className="ml-auto" onClick={() => sync(c.provider)} disabled={!!busy || !tid}>
                  {busy === c.provider ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : <RefreshCw className="size-3.5" aria-hidden />}
                  {busy === c.provider ? "Syncing…" : "Sync now"}
                </Button>
              </div>

              <p className="text-muted-foreground num text-xs">
                {ago(c.last_synced_at)}
                {c.sync_count > 0 && <> · {c.sync_count} {c.sync_count === 1 ? "sync" : "syncs"}
                  {c.kind === "orders" ? ` · ${c.orders_total.toLocaleString("en-IN")} orders` : ` · ${c.spend_rows_total} spend rows`}</>}
              </p>

              {outcome[c.provider]?.ok === true && (
                <div role="status" className="border-positive/30 bg-positive/5 text-positive flex gap-2 rounded-md border p-2.5 text-xs">
                  <CheckCircle2 className="mt-0.5 size-3.5 shrink-0" aria-hidden />{outcome[c.provider].text}. Dashboard updated.
                </div>
              )}
              {outcome[c.provider]?.ok === false && (
                <div role="alert" className="border-risk/30 bg-risk/5 text-risk flex gap-2 rounded-md border p-2.5 text-xs">
                  <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />{outcome[c.provider].text}
                </div>
              )}

              {c.provider === "webhook" && tid && <WebhookHelp tenant={tid} />}
            </li>
          ))}
        </ul>
      </SheetContent>
    </Sheet>
  );
}

function WebhookHelp({ tenant }: { tenant: string }) {
  const [copied, setCopied] = useState(false);
  const cmd = `curl -X POST ${API_BASE}/api/webhooks/orders \\
  -H "X-Tenant-ID: ${tenant}" -H "Content-Type: application/json" \\
  -d '{"orders":[{"order_id":"WH-1001","order_date":"${new Date().toISOString().slice(0, 10)}","customer_id":"c_1",
  "channel":"meta","lines":[{"sku":"<your sku>","quantity":1,"unit_price":799}]}]}'`;
  return (
    <details className="text-xs">
      <summary className="text-muted-foreground hover:text-foreground cursor-pointer rounded outline-none focus-visible:ring-2 focus-visible:ring-ring">
        Send your own order with curl
      </summary>
      <div className="relative mt-2">
        <pre className="bg-muted num overflow-x-auto rounded-md p-2.5 pr-9 text-[11px] leading-relaxed whitespace-pre-wrap break-all">{cmd}</pre>
        <Button variant="ghost" size="icon-sm" className="absolute top-1 right-1" aria-label={copied ? "Copied" : "Copy command"}
                onClick={() => { void navigator.clipboard?.writeText(cmd).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); }); }}>
          {copied ? <CheckCircle2 className="size-3.5" aria-hidden /> : <Copy className="size-3.5" aria-hidden />}
        </Button>
      </div>
      <p className="text-muted-foreground mt-1.5">Same validation as a CSV upload. Re-sending an order_id replaces it. The SKU must exist.</p>
    </details>
  );
}
