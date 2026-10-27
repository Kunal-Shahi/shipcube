import { AlertTriangle } from "lucide-react";
import type { ActionError } from "@/lib/errors";

export function ActionErrorText({ error, message }: { error: ActionError | null; message: string | null }) {
  if (!error || !message) return null;
  return (
    <p role="alert" className="flex items-start gap-1.5 text-[12px] text-critical">
      <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
      <span>
        {message}
        {error.requestId && <span className="ml-1 font-mono text-fg-subtle">({error.requestId})</span>}
      </span>
    </p>
  );
}
