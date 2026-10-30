"use client";

import { Check, X } from "lucide-react";
import { useState } from "react";
import { ActionErrorText } from "@/components/action-error";
import { Button, Pill } from "@/components/ui";
import { useAction } from "@/components/use-action";
import { ago } from "@/lib/format";
import type { WaitlistRequest } from "@/lib/types";
import { decideWaitlistAction } from "../../actions";

const TONE = { pending: "neutral", approved: "good", rejected: "critical" } as const;
const LABEL = { pending: "Pending", approved: "Approved", rejected: "Rejected" } as const;

export function WaitlistTable({ initial }: { initial: WaitlistRequest[] }) {
  const [rows, setRows] = useState(initial);
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] text-left">
        <thead className="text-[12px] text-fg-muted">
          <tr className="border-b border-border">
            <th className="px-4 py-2 font-medium">Person</th>
            <th className="px-4 py-2 font-medium">What they&apos;ll build</th>
            <th className="px-4 py-2 font-medium whitespace-nowrap">Requested</th>
            <th className="px-4 py-2 font-medium">Status</th>
            <th className="px-4 py-2" />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <Row key={r.id} r={r} onDecided={(next) => setRows((all) => all.map((x) => (x.id === next.id ? next : x)))} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Row({ r, onDecided }: { r: WaitlistRequest; onDecided: (r: WaitlistRequest) => void }) {
  const decide = useAction(decideWaitlistAction);
  const [which, setWhich] = useState<"approve" | "reject" | null>(null);
  const go = async (d: "approve" | "reject") => {
    setWhich(d);
    const next = await decide.run(r.id, d);
    if (next) onDecided(next);
  };
  return (
    <tr className="border-b border-border align-top last:border-0">
      <td className="min-w-[180px] px-4 py-2.5">
        <div className="font-medium break-words">{r.name}</div>
        <div className="text-[12px] break-all text-fg-muted">{r.email}</div>
      </td>
      <td className="max-w-[46ch] px-4 py-2.5 break-words">{r.useCase}</td>
      <td className="px-4 py-2.5 whitespace-nowrap text-fg-muted">{ago(r.createdAt)}</td>
      <td className="px-4 py-2.5">
        <Pill tone={TONE[r.status]}>{LABEL[r.status]}</Pill>
        {r.decidedBy && <div className="mt-1 text-[12px] text-fg-muted">by {r.decidedBy}</div>}
      </td>
      <td className="px-4 py-2.5 text-right whitespace-nowrap">
        {r.status === "pending" && (
          <span className="inline-flex items-center gap-2">
            <Button size="sm" variant="primary" pending={decide.pending && which === "approve"} disabled={decide.pending} onClick={() => go("approve")}>
              <Check className="size-3.5" aria-hidden /> Approve<span className="sr-only"> {r.email}</span>
            </Button>
            <Button size="sm" variant="ghost" pending={decide.pending && which === "reject"} disabled={decide.pending} onClick={() => go("reject")}>
              <X className="size-3.5" aria-hidden /> Reject<span className="sr-only"> {r.email}</span>
            </Button>
          </span>
        )}
        <div className="mt-1">
          <ActionErrorText error={decide.error} message={decide.message} />
        </div>
      </td>
    </tr>
  );
}
