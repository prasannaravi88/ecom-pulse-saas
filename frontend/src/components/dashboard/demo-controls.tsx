"use client";

import { Loader2, RotateCcw } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverDescription, PopoverTitle, PopoverTrigger } from "@/components/ui/popover";
import type { DemoStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

function useReset(onReset: () => Promise<void>) {
  const [state, setState] = useState<"idle" | "busy" | "done" | "error">("idle");
  const run = async () => {
    setState("busy");
    try {
      await onReset();
      setState("done");
      setTimeout(() => setState("idle"), 2500);
    } catch {
      setState("error");
    }
  };
  return { state, run };
}

/** Subtle "Live Sandbox" pill; tapping it explains the sandbox and offers Reset Demo (all breakpoints). */
export function SandboxBadge({ status, onReset }: { status: DemoStatus; onReset: () => Promise<void> }) {
  const { state, run } = useReset(onReset);
  const modified = status.active_sandboxes.length > 0;
  return (
    <Popover>
      <PopoverTrigger
        render={
          <button type="button" data-testid="sandbox-badge"
                  className="border-positive/25 bg-positive/5 text-positive hover:bg-positive/8 inline-flex h-6 shrink-0 items-center gap-1.5 rounded-full border px-2 text-[11px] font-medium outline-none focus-visible:ring-2 focus-visible:ring-ring" />
        }
      >
        <span className="relative flex size-1.5" aria-hidden>
          <span className="bg-positive absolute inline-flex size-full animate-ping rounded-full opacity-60 motion-reduce:animate-none" />
          <span className="bg-positive relative inline-flex size-1.5 rounded-full" />
        </span>
        <span className="max-sm:hidden">Live Sandbox</span>
        <span className="sm:hidden">Sandbox</span>
        {modified && <span className="sr-only">(you have uploaded changes)</span>}
      </PopoverTrigger>
      <PopoverContent align="start" className="w-72 text-xs">
        <PopoverTitle className="text-sm">Live Sandbox</PopoverTitle>
        <PopoverDescription className="text-xs leading-relaxed">
          This is a public demo. CSV uploads go into a private copy for this browser only, kept for{" "}
          {status.sandbox_ttl_hours}h. The shared Lumière Botanicals and Northbound Coffee Co. data is never changed.
        </PopoverDescription>
        <p className="text-muted-foreground mt-2">
          {modified
            ? `Your uploads are active for: ${status.active_sandboxes.map((s) => s.tenant).join(", ")}.`
            : "You're viewing the clean seed data."}
        </p>
        <Button size="sm" variant="outline" className="mt-3 w-full" onClick={run} disabled={state === "busy"}>
          {state === "busy" ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : <RotateCcw className="size-3.5" aria-hidden />}
          {state === "done" ? "Demo restored" : "Reset Demo"}
        </Button>
        {state === "error" && <p role="alert" className="text-risk mt-2">Reset failed. Try again.</p>}
      </PopoverContent>
    </Popover>
  );
}

/** Always-visible Reset Demo button for wider screens. */
export function ResetDemoButton({ status, onReset, className }: {
  status: DemoStatus; onReset: () => Promise<void>; className?: string;
}) {
  const { state, run } = useReset(onReset);
  const modified = status.active_sandboxes.length > 0;
  return (
    <Button variant="ghost" size="sm" onClick={run} disabled={state === "busy"} className={cn("text-muted-foreground", className)}
            title={modified ? "Discard your uploads and restore the clean seed data" : "Restore the clean seed data"}>
      {state === "busy" ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : <RotateCcw className="size-3.5" aria-hidden />}
      <span aria-live="polite">{state === "done" ? "Restored" : "Reset Demo"}</span>
      {modified && state !== "done" && <span className="bg-primary size-1.5 rounded-full" aria-label="you have uploaded changes" />}
    </Button>
  );
}
