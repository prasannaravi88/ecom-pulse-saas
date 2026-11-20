import { Suspense } from "react";

import { Dashboard } from "@/components/dashboard/dashboard";

// The dashboard reads tenant / range / currency from the URL (client-side), so it
// renders inside a Suspense boundary as required for useSearchParams.
export default function Home() {
  return (
    <Suspense fallback={<div className="text-muted-foreground p-8 text-sm">Loading dashboard…</div>}>
      <Dashboard />
    </Suspense>
  );
}
