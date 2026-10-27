"use client";

import { Check, X } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui";
import type { Migration } from "@/lib/types";

export function ApplyPanel({ m, nextVersion }: { m: Migration; nextVersion: number }) {
  const [state, setState] = useState<"idle" | "applying" | "rejecting" | "applied" | "rejected">("idle");
  const [ack, setAck] = useState(false);
  const [note, setNote] = useState("");
  const breaking = m.impact.breaks.length > 0;

  if (state === "applied")
    return (
      <div role="status" className="flex flex-col gap-1.5 p-4">
        <div className="flex items-center gap-2 font-semibold text-good"><Check className="size-4" aria-hidden /> Applied as version {nextVersion}</div>
        <p className="text-fg-muted">
          New queries use version {nextVersion} now. {m.proposedBy.agent} sees the change the next time it calls data_describe_cube.
        </p>
      </div>
    );
  if (state === "rejected")
    return (
      <div role="status" className="flex flex-col gap-1.5 p-4">
        <div className="flex items-center gap-2 font-semibold text-critical"><X className="size-4" aria-hidden /> Rejected</div>
        <p className="text-fg-muted">The model stays at version {nextVersion - 1}. The agent sees the rejection{note.trim() ? " and your note" : ""} if it checks.</p>
      </div>
    );

  return (
    <div className="flex flex-col gap-3 p-4">
      {breaking && (
        <label className="flex cursor-pointer items-start gap-2 rounded-md border border-critical/40 bg-critical-soft px-3 py-2">
          <input type="checkbox" className="mt-[3px] size-3.5 accent-[var(--critical)]" checked={ack} onChange={(e) => setAck(e.target.checked)} />
          <span>
            Apply anyway. {m.impact.breaks.length} saved {m.impact.breaks.length === 1 ? "query" : "queries"} in {m.impact.appsBroken.join(", ")} will stop working until{" "}
            {m.impact.appsBroken.length === 1 ? "it is" : "they are"} updated.
          </span>
        </label>
      )}
      <div className="flex flex-col gap-1.5">
        <label htmlFor="mig-note" className="text-[12px] font-medium">
          Note <span className="font-normal text-fg-muted">(optional, saved with the decision)</span>
        </label>
        <textarea
          id="mig-note"
          rows={2}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          className="w-full resize-y rounded-md border border-border-strong bg-surface px-2.5 py-2 text-[13px] focus:border-accent focus:ring-2 focus:ring-accent/25 focus:outline-none"
        />
      </div>
      <Button
        variant="primary"
        className="w-full"
        disabled={(breaking && !ack) || state !== "idle"}
        pending={state === "applying"}
        onClick={() => {
          setState("applying");
          setTimeout(() => setState("applied"), 800);
        }}
      >
        Apply to the model
      </Button>
      <Button
        className="w-full"
        disabled={state !== "idle"}
        pending={state === "rejecting"}
        onClick={() => {
          setState("rejecting");
          setTimeout(() => setState("rejected"), 500);
        }}
      >
        Reject
      </Button>
      <p className="text-[12px] text-fg-muted">
        Applying re-checks for conflicts first. If another change landed since this was proposed, ShipCube asks the agent to rebase it.
      </p>
    </div>
  );
}
