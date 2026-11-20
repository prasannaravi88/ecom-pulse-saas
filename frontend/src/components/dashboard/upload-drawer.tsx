"use client";

import { CheckCircle2, Download, FileUp, Loader2, TriangleAlert } from "lucide-react";
import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { api, ApiError, type DemoStatus, type Tenant, type UploadError } from "@/lib/api";
import { cn } from "@/lib/utils";

import { Segmented } from "./segmented";

type Kind = "orders" | "ad-spend";

const SPEC: Record<Kind, { required: string[]; optional: string[]; note: string }> = {
  orders: {
    required: ["order_id", "order_date", "customer_id", "sku", "quantity", "unit_price"],
    optional: ["discount", "refund_amount", "unit_cogs", "status", "payment_method", "channel", "shipping_cost", "payment_fee"],
    note: "One row per line item. Shopify “Export orders” headers (Name, Created at, Email, Lineitem sku…) are mapped automatically. Re-uploading an order_id replaces it. New SKUs need unit_cogs.",
  },
  "ad-spend": {
    required: ["date", "channel", "campaign", "spend"],
    optional: ["sku", "impressions", "clicks", "platform_reported_revenue"],
    note: "One row per day × channel × campaign; existing rows are replaced. Add sku to attribute a campaign's spend to one product.",
  },
};

type Result =
  | { ok: true; summary: string }
  | { ok: false; message: string; errors: UploadError[]; columns?: string[] | null };

export function UploadDrawer({ open, onOpenChange, tenant, demo, onUploaded }: {
  open: boolean; onOpenChange: (o: boolean) => void; tenant: Tenant | null; demo: DemoStatus | null; onUploaded: () => void;
}) {
  const [kind, setKind] = useState<Kind>("orders");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const pick = (f: File | null | undefined) => { setFile(f ?? null); setResult(null); };

  async function submit() {
    if (!file || !tenant) return;
    setBusy(true);
    setResult(null);
    try {
      const r = await api.upload(tenant.id, kind, file);
      const summary = kind === "orders"
        ? `${r.rows} rows → ${r.orders_inserted} new orders, ${r.orders_replaced} replaced` +
          ((r.products_created as string[])?.length ? `, new SKUs: ${(r.products_created as string[]).join(", ")}` : "")
        : `${r.rows} rows → ${r.rows_inserted} new, ${r.rows_replaced} replaced`;
      setResult({ ok: true, summary: r.sandboxed ? `${summary} (in your sandbox)` : summary });
      setFile(null);
      if (input.current) input.current.value = "";
      onUploaded();
    } catch (e) {
      const err = e as ApiError;
      const d = err.detail as { errors?: UploadError[]; columns_found?: string[] } | undefined;
      setResult({ ok: false, message: err.message, errors: d?.errors ?? [], columns: d?.columns_found });
    } finally {
      setBusy(false);
    }
  }

  const spec = SPEC[kind];
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full gap-0 data-[side=right]:w-full overflow-y-auto data-[side=right]:sm:max-w-md">
        <SheetHeader className="border-b">
          <SheetTitle>Upload CSV</SheetTitle>
          <SheetDescription>
            Into <b className="text-foreground">{tenant?.name ?? "…"}</b> ({tenant?.currency}). The whole file is checked first;
            if any row is invalid nothing is written. Metrics recompute right after.
            {demo && (
              <span className="border-positive/25 bg-positive/5 text-foreground mt-2 block rounded-md border px-2.5 py-1.5">
                Live Sandbox: this goes into a private copy for your browser ({demo.sandbox_ttl_hours}h). The shared demo
                data isn&apos;t changed; use Reset Demo to start clean.
              </span>
            )}
          </SheetDescription>
        </SheetHeader>
        <div className="flex flex-col gap-4 p-4">
          <Segmented label="Upload type" value={kind} onChange={(k) => { setKind(k); pick(null); }}
                     options={[{ value: "orders", label: "Orders" }, { value: "ad-spend", label: "Ad spend" }]} />

          <label
            onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => { e.preventDefault(); setDrag(false); pick(e.dataTransfer.files?.[0]); }}
            className={cn("flex cursor-pointer flex-col items-center gap-2 rounded-lg border border-dashed px-4 py-8 text-center transition-colors",
                          drag ? "border-primary bg-accent" : "hover:bg-muted/50")}>
            <FileUp className="text-muted-foreground size-6" aria-hidden />
            <span className="text-sm font-medium">{file ? file.name : "Drop a .csv here or click to choose"}</span>
            <span className="text-muted-foreground text-xs">
              {file ? `${(file.size / 1024).toFixed(1)} KB` : "Up to 20 MB"}
            </span>
            <input ref={input} type="file" accept=".csv,text/csv" className="sr-only"
                   onChange={(e) => pick(e.target.files?.[0])} />
          </label>

          <div className="flex items-center gap-2">
            <Button onClick={submit} disabled={!file || busy || !tenant} className="flex-1">
              {busy && <Loader2 className="size-4 animate-spin" aria-hidden />}
              {busy ? "Validating & ingesting…" : `Upload ${kind === "orders" ? "orders" : "ad spend"}`}
            </Button>
            <Button variant="outline" nativeButton={false} render={<a href={api.templateUrl(kind)} download />}>
              <Download className="size-4" aria-hidden />Template
            </Button>
          </div>

          {result?.ok && (
            <div role="status" className="border-positive/30 bg-positive/5 text-positive flex gap-2 rounded-md border p-3 text-sm">
              <CheckCircle2 className="mt-0.5 size-4 shrink-0" aria-hidden />
              <div><div className="font-medium">Ingested</div><div className="num text-xs">{result.summary}</div></div>
            </div>
          )}
          {result && !result.ok && (
            <div role="alert" className="border-risk/30 bg-risk/5 rounded-md border p-3 text-sm">
              <div className="text-risk flex items-center gap-2 font-medium">
                <TriangleAlert className="size-4" aria-hidden />Nothing was written: {result.message}
              </div>
              {result.errors.length > 0 && (
                <ul className="mt-2 max-h-56 space-y-1 overflow-y-auto text-xs">
                  {result.errors.map((e, i) => (
                    <li key={i} className="num">
                      <span className="text-muted-foreground">{e.row ? `Row ${e.row}` : "File"}{e.column ? ` · ${e.column}` : ""}:</span>{" "}
                      <span className="font-sans">{e.message}</span>
                    </li>
                  ))}
                </ul>
              )}
              {result.columns && <p className="text-muted-foreground mt-2 text-xs">Columns found: {result.columns.join(", ")}</p>}
            </div>
          )}

          <div className="text-xs">
            <div className="mb-1 font-medium">Required columns</div>
            <div className="flex flex-wrap gap-1">
              {spec.required.map((c) => <code key={c} className="bg-muted rounded px-1.5 py-0.5 font-mono text-[11px]">{c}</code>)}
            </div>
            <div className="mt-3 mb-1 font-medium">Optional</div>
            <div className="flex flex-wrap gap-1">
              {spec.optional.map((c) => <code key={c} className="text-muted-foreground rounded border px-1.5 py-0.5 font-mono text-[11px]">{c}</code>)}
            </div>
            <p className="text-muted-foreground mt-3 leading-relaxed">{spec.note}</p>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
