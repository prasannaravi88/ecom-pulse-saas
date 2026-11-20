"use client";

import { createContext, useContext, useMemo } from "react";

import { convert, formatAxisMoney, formatMoney, type Currency } from "@/lib/format";

interface Display {
  tenantCurrency: Currency;
  displayCurrency: Currency;
  usdInr: number;
  converted: boolean;
  /** Convert a tenant-currency amount into the display currency. */
  toDisplay: (v: number) => number;
  money: (v: number | null | undefined, compact?: boolean) => string;
  axisMoney: (v: number) => string;
}

const Ctx = createContext<Display | null>(null);

export function DisplayProvider({ tenantCurrency, displayCurrency, usdInr, children }: {
  tenantCurrency: Currency; displayCurrency: Currency; usdInr: number; children: React.ReactNode;
}) {
  const value = useMemo<Display>(() => {
    const toDisplay = (v: number) => convert(v, tenantCurrency, displayCurrency, usdInr);
    return {
      tenantCurrency, displayCurrency, usdInr,
      converted: tenantCurrency !== displayCurrency,
      toDisplay,
      money: (v, compact = true) => (v == null ? formatMoney(v, displayCurrency) : formatMoney(toDisplay(v), displayCurrency, compact)),
      // Axis values are already converted (charts plot display-currency data).
      axisMoney: (v) => formatAxisMoney(v, displayCurrency),
    };
  }, [tenantCurrency, displayCurrency, usdInr]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useDisplay(): Display {
  const v = useContext(Ctx);
  if (!v) throw new Error("useDisplay outside DisplayProvider");
  return v;
}
