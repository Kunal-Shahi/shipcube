"use client";

import { useCallback, useState } from "react";
import { describeError, type ActionError, type ActionResult } from "@/lib/errors";

/** Runs a server action and tracks pending and error state for the UI. */
export function useAction<A extends unknown[], T>(action: (...args: A) => Promise<ActionResult<T>>) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ActionError | null>(null);

  const run = useCallback(
    async (...args: A): Promise<T | undefined> => {
      setPending(true);
      setError(null);
      try {
        const r = await action(...args);
        if (r.ok) return r.data;
        setError(r.error);
      } catch {
        // The action itself couldn't be reached (network, deploy in progress).
        setError({ code: "network", message: "Couldn't reach the console server.", requestId: "" });
      } finally {
        setPending(false);
      }
      return undefined;
    },
    [action],
  );

  return { run, pending, error, message: error ? describeError(error) : null, clearError: () => setError(null) };
}

/** A fresh key per user intent, so a retried request isn't applied twice. */
export function newIdempotencyKey(prefix: string) {
  return `${prefix}-${crypto.randomUUID()}`;
}
