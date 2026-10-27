"use client";

import { Check, Clock, X } from "lucide-react";
import { useState } from "react";
import { APPROVAL_KIND } from "@/components/approval-meta";
import { Button } from "@/components/ui";
import { cn } from "@/lib/cn";
import type { Approval } from "@/lib/types";

const AFTER: Record<Approval["kind"], string> = {
  linked_gcp_deploy: "continues the deploy and reports the URL when it's live.",
  cap_raise: "is told the new cap is in place.",
  domain_grant: "is told the domain was added to the access list.",
  delete_app: "is told the app is deleted. You can restore it from Apps for 7 days.",
};

export function DecisionPanel({ approval, hoursLeft }: { approval: Approval; hoursLeft: number }) {
  const [decision, setDecision] = useState<"approved" | "rejected" | null>(null);
  const [pending, setPending] = useState<"approve" | "reject" | null>(null);
  const [note, setNote] = useState("");
  const agent = approval.requestedBy.agent;
  const danger = approval.kind === "delete_app";

  function decide(d: "approve" | "reject") {
    setPending(d);
    setTimeout(() => {
      setPending(null);
      setDecision(d === "approve" ? "approved" : "rejected");
    }, 600);
  }

  if (decision) {
    const ok = decision === "approved";
    return (
      <div role="status" className="flex flex-col gap-2 p-4">
        <div className={cn("flex items-center gap-2 font-semibold", ok ? "text-good" : "text-critical")}>
          {ok ? <Check className="size-4" aria-hidden /> : <X className="size-4" aria-hidden />}
          {ok ? (danger ? "Deleted" : "Approved") : "Rejected"}
        </div>
        <p className="text-fg-muted">
          {ok
            ? `${agent} ${AFTER[approval.kind]}`
            : `${agent} gets status "rejected"${note.trim() ? " with your note" : ""} and won't retry on its own.`}
        </p>
        {!ok && note.trim() && <blockquote className="rounded-md bg-surface-2 px-3 py-2">{note}</blockquote>}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 p-4">
      <div className="flex items-center gap-1.5 text-[12px] text-fg-muted">
        <Clock className="size-3.5" aria-hidden />
        <span className="num">Expires in {hoursLeft} h.</span> {agent} is waiting.
      </div>
      <div className="flex flex-col gap-1.5">
        <label htmlFor="decision-note" className="text-[12px] font-medium">
          Note to the agent <span className="font-normal text-fg-muted">(optional)</span>
        </label>
        <textarea
          id="decision-note"
          rows={3}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder={approval.kind === "cap_raise" ? "For example: use $70, that covers the forecast" : "The agent sees this with your decision"}
          className="w-full resize-y rounded-md border border-border-strong bg-surface px-2.5 py-2 text-[13px] placeholder:text-fg-subtle focus:border-accent focus:ring-2 focus:ring-accent/25 focus:outline-none"
        />
      </div>
      <div className="flex flex-col gap-2">
        <Button variant={danger ? "danger" : "primary"} className={cn("w-full", danger && "border-critical")} onClick={() => decide("approve")} pending={pending === "approve"} disabled={!!pending}>
          {APPROVAL_KIND[approval.kind].verb}
        </Button>
        <Button variant="secondary" className="w-full" onClick={() => decide("reject")} pending={pending === "reject"} disabled={!!pending}>
          Reject
        </Button>
      </div>
      <p className="text-[12px] text-fg-muted">
        Only a person signed in to the console can decide. Agents and API tokens can&apos;t approve their own requests.
      </p>
    </div>
  );
}
