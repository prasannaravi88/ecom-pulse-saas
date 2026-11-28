"use client";

import { AlertTriangle } from "lucide-react";
import { usePathname, useSearchParams } from "next/navigation";
import { useCallback, useState } from "react";

import { useApi } from "@/hooks/use-api";
import { api, API_BASE, RANGES, type RangePreset } from "@/lib/api";
import { formatDay, type Currency } from "@/lib/format";

import { AnalystDrawer } from "./analyst-drawer";
import { ConnectDrawer } from "./connect-drawer";
import { CohortHeatmap } from "./cohort-heatmap";
import { DisplayProvider } from "./display-context";
import { Guardrails } from "./guardrails";
import { KpiStrip } from "./kpi-strip";
import { MarginChart } from "./margin-chart";
import { TopBar } from "./top-bar";
import { UploadDrawer } from "./upload-drawer";

export function Dashboard() {
  const pathname = usePathname();
  const params = useSearchParams();

  const tenants = useApi(api.tenants, "tenants");
  const fx = useApi(api.fx, "fx");
  const [version, setVersion] = useState(0);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [connectOpen, setConnectOpen] = useState(false);
  const [analystOpen, setAnalystOpen] = useState(false);

  const list = tenants.data ?? [];
  const tenant = list.find((t) => t.id === params.get("tenant")) ?? list[0] ?? null;
  const rangeParam = params.get("range") as RangePreset | null;
  const range: RangePreset = rangeParam && RANGES.includes(rangeParam) ? rangeParam : "30D";
  const ccyParam = params.get("ccy");
  const currency: Currency = ccyParam === "INR" || ccyParam === "USD" ? ccyParam : (tenant?.currency ?? "INR");

  const setParams = useCallback((patch: Record<string, string | null>) => {
    // Read the live URL, not the render-time snapshot: two quick changes (e.g. currency
    // then range) must both survive even before the router has re-rendered.
    const next = new URLSearchParams(window.location.search);
    for (const [k, v] of Object.entries(patch)) {
      if (v == null) next.delete(k);
      else next.set(k, v);
    }
    // Native history API: synchronous, and Next.js keeps useSearchParams in sync with it.
    const qs = next.toString();
    window.history.replaceState(null, "", qs ? `${pathname}?${qs}` : pathname);
  }, [pathname]);

  const demo = useApi(api.demoStatus, `demo|${version}`);
  const resetDemo = useCallback(async () => {
    await api.resetDemo();
    setVersion((v) => v + 1);
  }, []);

  const tid = tenant?.id ?? null;
  const k = `${tid}|${range}|${version}`;
  const overview = useApi(tid ? () => api.overview(tid, range) : null, `ov|${k}`);
  const daily = useApi(tid ? () => api.daily(tid, range) : null, `daily|${k}`);
  const skus = useApi(tid ? () => api.skus(tid, range) : null, `skus|${k}`);
  const channels = useApi(tid ? () => api.channels(tid, range) : null, `ch|${k}`);
  const cohorts = useApi(tid ? () => api.cohorts(tid) : null, `co|${tid}|${version}`);

  const errors = [tenants, overview, daily, skus, channels, cohorts].map((s) => s.error).filter(Boolean) as Error[];
  const period = overview.data?.period;
  const usdInr = fx.data?.rate ?? 84;

  return (
    <DisplayProvider tenantCurrency={tenant?.currency ?? "INR"} displayCurrency={currency} usdInr={usdInr}>
      <TopBar
        tenants={list} tenantId={tid}
        onTenant={(id) => setParams({ tenant: id, ccy: null })}
        currency={currency} onCurrency={(c) => setParams({ ccy: c === tenant?.currency ? null : c })}
        range={range} onRange={(r) => setParams({ range: r === "30D" ? null : r })}
        onUpload={() => setUploadOpen(true)} onConnect={() => setConnectOpen(true)} onAnalyst={() => setAnalystOpen(true)}
        demo={demo.data} onResetDemo={resetDemo}
      />
      <main className="mx-auto w-full max-w-[1400px] space-y-4 px-4 py-5 md:px-6">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">{tenant?.name ?? "Loading…"}</h1>
            <p className="text-muted-foreground num text-xs">
              {period
                ? <>{formatDay(period.start)} – {formatDay(period.end)} vs {formatDay(period.previous_start)} – {formatDay(period.previous_end)}</>
                : " "}
            </p>
          </div>
          {tenant && currency !== tenant.currency && (
            <p className="text-muted-foreground text-xs">
              Shown in {currency} at a fixed ₹{usdInr}/$ for display. Books are in {tenant.currency}.
            </p>
          )}
        </div>

        {errors.length > 0 && (
          <div role="alert" className="border-risk/30 bg-risk/5 text-risk flex items-start gap-2 rounded-lg border p-3 text-sm">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
            <div>Couldn&apos;t load some data from the API: {errors[0].message}. Check that {API_BASE || "NEXT_PUBLIC_API_URL"} is reachable and allows this origin (ALLOWED_ORIGINS).</div>
          </div>
        )}

        <KpiStrip data={overview.data} loading={overview.loading} />
        <MarginChart overview={overview.data} daily={daily.data} />
        <CohortHeatmap data={cohorts.data} />
        <Guardrails skus={skus.data} channels={channels.data} />
      </main>

      <UploadDrawer open={uploadOpen} onOpenChange={setUploadOpen} tenant={tenant}
                    demo={demo.data?.demo_mode ? demo.data : null}
                    onUploaded={() => setVersion((v) => v + 1)} />
      <ConnectDrawer open={connectOpen} onOpenChange={setConnectOpen} tenant={tenant}
                     demo={demo.data?.demo_mode ? demo.data : null}
                     onSynced={() => setVersion((v) => v + 1)} />
      <AnalystDrawer open={analystOpen} onOpenChange={setAnalystOpen} tenant={tenant} range={range}
                     displayCurrency={currency} dataVersion={version} />
    </DisplayProvider>
  );
}
