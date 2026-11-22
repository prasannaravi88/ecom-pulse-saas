"use client";

import { cn } from "@/lib/utils";

/** Single-select segmented control (radio-group semantics). */
export function Segmented<T extends string>({ value, options, onChange, label, size = "default", className }: {
  value: T;
  options: { value: T; label: React.ReactNode; title?: string }[];
  onChange: (v: T) => void;
  label: string;
  size?: "sm" | "default";
  className?: string;
}) {
  return (
    <div role="radiogroup" aria-label={label}
         className={cn("inline-flex items-center rounded-lg border bg-card p-0.5", className)}>
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button key={o.value} type="button" role="radio" aria-checked={active} title={o.title}
                  onClick={() => onChange(o.value)}
                  className={cn(
                    "rounded-md font-medium whitespace-nowrap transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    size === "sm" ? "h-6 px-2 text-xs" : "h-7 px-2.5 text-[13px]",
                    active ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground",
                  )}>
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
