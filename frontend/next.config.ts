import type { NextConfig } from "next";
import { PHASE_PRODUCTION_BUILD } from "next/constants";

export default function config(phase: string): NextConfig {
  // NEXT_PUBLIC_API_URL is inlined into the client bundle at build time, so a production
  // build without it would ship a dashboard that can't reach the API. Fail loudly instead.
  if (phase === PHASE_PRODUCTION_BUILD && !process.env.NEXT_PUBLIC_API_URL) {
    throw new Error(
      "NEXT_PUBLIC_API_URL is not set. Point it at the deployed FastAPI backend, " +
        "e.g. NEXT_PUBLIC_API_URL=https://ecom-pulse-api.up.railway.app",
    );
  }
  return {
    // No floating Next.js dev badge: it sat over the mobile Analyst input.
    devIndicators: false,
    cacheComponents: true,
    partialPrefetching: true,
    turbopack: {
      rules: {
        "*.css": {
          loaders: ["@tailwindcss/turbopack"],
          as: "*.css",
        },
      },
    },
  };
}
