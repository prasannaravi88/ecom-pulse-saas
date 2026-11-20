const KEY = "ecom-pulse:sandbox-id";

/**
 * A random id for this browser, sent as X-Sandbox-ID. In demo mode the backend routes this
 * browser's uploads to a private copy of the tenant; other visitors keep seeing the clean seed.
 */
export function getSandboxId(): string | null {
  if (typeof window === "undefined") return null;
  try {
    let id = window.localStorage.getItem(KEY);
    if (!id || !/^[A-Za-z0-9-]{16,64}$/.test(id)) {
      id = typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join("");
      window.localStorage.setItem(KEY, id);
    }
    return id;
  } catch {
    return null; // storage blocked (private mode): demo uploads will be refused with a clear message
  }
}
