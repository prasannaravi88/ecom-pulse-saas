"use client";

import { useEffect, useState } from "react";

export interface ApiState<T> { data: T | null; error: Error | null; loading: boolean }

/**
 * Fetch whenever `key` changes. Keeps the previous data while a new request is in
 * flight (so filters don't flash empty) and ignores responses for stale keys.
 */
export function useApi<T>(fetcher: (() => Promise<T>) | null, key: string): ApiState<T> {
  const [state, setState] = useState<{ data: T | null; error: Error | null; key: string | null }>(
    { data: null, error: null, key: null },
  );

  useEffect(() => {
    if (!fetcher) return;
    let live = true;
    fetcher().then(
      (data) => live && setState({ data, error: null, key }),
      (error: Error) => live && setState((s) => ({ data: s.data, error, key })),
    );
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `key` captures the fetcher's inputs
  }, [key]);

  return { data: state.data, error: state.error, loading: !!fetcher && state.key !== key };
}
