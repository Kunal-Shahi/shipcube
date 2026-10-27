"use client";

import { AlertTriangle, RotateCw } from "lucide-react";
import { Button } from "./ui";

export function ErrorState({
  title,
  error,
  reset,
}: {
  title: string;
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div role="alert" className="flex flex-col items-start gap-2 rounded-lg border border-border bg-surface px-6 py-8">
      <div className="flex items-center gap-2 text-critical">
        <AlertTriangle className="size-4" aria-hidden />
        <h2 className="text-[14px] font-semibold">{title}</h2>
      </div>
      <p className="max-w-[62ch] text-fg-muted">
        The control-plane API didn&apos;t respond. Your deployed apps keep running; only the console is affected. Try
        again, and if it keeps failing, include the request ID when you report it.
      </p>
      {error.digest && (
        <p className="font-mono text-[12px] text-fg-subtle">Request ID {error.digest}</p>
      )}
      <Button className="mt-2" onClick={reset}>
        <RotateCw className="size-3.5" aria-hidden />
        Try again
      </Button>
    </div>
  );
}
