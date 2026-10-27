"use client";

import { Check, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ActionErrorText } from "@/components/action-error";
import { Button } from "@/components/ui";
import { useAction } from "@/components/use-action";
import type { Migration } from "@/lib/types";
import { decideMigrationAction } from "../../../actions";

export function ApplyPanel({ m, nextVersion }: { m: Migration; nextVersion: number }) {
  const [state, setState] = useState<"idle" | "applying" | "rejecting" | "applied" | "rejected">("idle");
  const [appliedVersion, setAppliedVersion] = useState(nextVersion);
  const action = useAction(decideMigrationAction);
  const router = useRouter();

  async function decide(d: "apply" | "reject") {
    setState(d === "apply" ? "applying" : "rejecting");
    const r = await action.run(m.id, d, { allowBreaking: ack, note: note.trim() || undefined });
    if (!r) return setState("idle");
    if (r.applied) setAppliedVersion(r.applied.version);
    setState(d === "apply" ? "applied" : "rejected");
    router.refresh();
  }
  const [ack, setAck] = useState(false);
  const [note, setNote] = useState("");
  const breaking = m.impact.breaks.length > 0;

  if (state === "applied")
    return (
      <div role="status" className="flex flex-col gap-1.5 p-4">
        <div className="flex items-center gap-2 font-semibold text-good"><Check className="size-4" aria-hidden /> Applied as version {appliedVersion}</div>
        <p className="text-fg-muted">
          New queries use version {appliedVersion} now. {m.proposedBy.agent} sees the change the next time it calls data_describe_cube.
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
          maxLength={500}
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
        onClick={() => decide("apply")}
      >
        Apply to the model
      </Button>
      <Button
        className="w-full"
        disabled={state !== "idle"}
        pending={state === "rejecting"}
        onClick={() => decide("reject")}
      >
        Reject
      </Button>
      <ActionErrorText error={action.error} message={action.message} />
      <p className="text-[12px] text-fg-muted">
        Applying re-checks for conflicts first. If another change landed since this was proposed, ShipCube asks the agent to rebase it.
      </p>
    </div>
  );
}
