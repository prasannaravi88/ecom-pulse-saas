import { cn } from "@/lib/utils";

export function Panel({ title, description, actions, children, className, id }: {
  title: string; description?: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode;
  className?: string; id?: string;
}) {
  return (
    <section id={id} aria-labelledby={id ? `${id}-title` : undefined}
             className={cn("bg-card min-w-0 rounded-lg border", className)}>
      <header className="flex flex-wrap items-start justify-between gap-3 border-b px-4 py-3">
        <div className="min-w-0">
          <h2 id={id ? `${id}-title` : undefined} className="text-[15px] font-semibold tracking-tight">{title}</h2>
          {description && <p className="text-muted-foreground mt-0.5 text-xs">{description}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </header>
      <div className="p-4">{children}</div>
    </section>
  );
}
