"use client";

import Link from "next/link";
import { useState } from "react";
import { ActionErrorText } from "@/components/action-error";
import { Button, Field, Input, Meter, Select } from "@/components/ui";
import { newIdempotencyKey, useAction } from "@/components/use-action";
import { count, money } from "@/lib/format";
import type { Caps } from "@/lib/types";
import { saveCapsAction } from "../../actions";

type Usage = { spend?: number; peakRpm?: number; peakConcurrency?: number; tokensToday?: number };
type NumKey = "monthlySpend" | "requestsPerMinute" | "concurrency" | "llmTokensPerDay" | "maxInstances";

export function CapsForm({ appId, caps, usage, appName }: { appId: string; caps: Caps; usage: Usage; appName: string }) {
  const [draft, setDraft] = useState(caps);
  const [saved, setSaved] = useState(caps);
  const [message, setMessage] = useState<string | null>(null);
  const [approvalUrl, setApprovalUrl] = useState<string | null>(null);
  const save = useAction(saveCapsAction);

  const changed = (Object.keys(draft) as (keyof Caps)[]).filter((k) => draft[k] !== saved[k]);
  const dirty = changed.length > 0;
  const spendError =
    usage.spend !== undefined && draft.monthlySpend < usage.spend
      ? `Already spent ${money(usage.spend)} this month. Set the cap above that, or the app stops now.`
      : null;

  const num = (k: NumKey) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setDraft({ ...draft, [k]: e.target.value === "" ? 0 : Number(e.target.value) });

  const rows: { key: NumKey; label: string; used?: number; usedLabel?: string; help: string; step?: number }[] = [
    {
      key: "monthlySpend",
      label: "Monthly spend ($)",
      used: usage.spend,
      usedLabel: usage.spend !== undefined ? `${money(usage.spend)} of ${money(draft.monthlySpend)} this month` : undefined,
      help: "The app stops serving when it reaches this.",
    },
    {
      key: "requestsPerMinute",
      label: "Requests per minute",
      used: usage.peakRpm,
      usedLabel: usage.peakRpm !== undefined ? `Peak ${count(usage.peakRpm)} in the last 7 days` : undefined,
      help: "Extra requests get HTTP 429.",
    },
    {
      key: "concurrency",
      label: "Concurrent requests per instance",
      used: usage.peakConcurrency,
      usedLabel: usage.peakConcurrency !== undefined ? `Peak ${usage.peakConcurrency} in the last 7 days` : undefined,
      help: "Cloud Run concurrency.",
    },
  ];
  if (caps.llmTokensPerDay !== undefined) {
    rows.push({
      key: "llmTokensPerDay",
      label: "LLM tokens per day",
      used: usage.tokensToday,
      usedLabel: usage.tokensToday !== undefined ? `${count(usage.tokensToday)} used today` : undefined,
      help: "Through the AI gateway. Resets at 00:00 UTC.",
    });
  }

  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        if (spendError || !dirty) return;
        setMessage(null);
        setApprovalUrl(null);
        const diff = Object.fromEntries(changed.map((k) => [k, draft[k]])) as Partial<Caps>;
        const r = await save.run(appId, diff, newIdempotencyKey("caps"));
        if (!r) return;
        if (r.approval) {
          setApprovalUrl(r.approval.url);
          setMessage("This raise is above the workspace default, so it waits for approval.");
        } else {
          setSaved(r.caps);
          setDraft(r.caps);
          setMessage(`Caps saved for ${appName}`);
        }
      }}
      className="flex flex-col"
    >
      <div className="grid gap-x-6 gap-y-5 p-4 sm:grid-cols-2">
        {rows.map((r) => (
          <Field key={r.key} label={r.label} htmlFor={`cap-${r.key}`} help={r.help} error={r.key === "monthlySpend" ? spendError : null}>
            <Input
              id={`cap-${r.key}`}
              type="number"
              min={0}
              step={r.step ?? 1}
              inputMode="numeric"
              className="num"
              value={draft[r.key] ?? 0}
              onChange={num(r.key)}
            />
            {r.used !== undefined && (
              <div className="mt-1 flex flex-col gap-1">
                <Meter value={r.used} max={draft[r.key] ?? 0} label={r.label} />
                <span className="num text-[12px] text-fg-muted">{r.usedLabel}</span>
              </div>
            )}
          </Field>
        ))}
        <Field label="Max instances" htmlFor="cap-maxInstances" help="Upper bound for autoscaling, at most 10.">
          <Input id="cap-maxInstances" type="number" min={0} max={10} className="num" value={draft.maxInstances} onChange={num("maxInstances")} />
        </Field>
        <Field label="Memory per instance" htmlFor="cap-memory" help="From the load estimate. Raising it raises cost.">
          <Select id="cap-memory" value={draft.memory} onChange={(e) => setDraft({ ...draft, memory: e.target.value })}>
            {["256 MiB", "512 MiB", "1 GiB", "2 GiB", "4 GiB", "8 GiB"].map((m) => (
              <option key={m}>{m}</option>
            ))}
          </Select>
        </Field>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-4 py-3">
        <p className="text-[12px] text-fg-muted">
          Your agent can change these too, with the <code className="font-mono">set_caps</code> tool. Raises above the workspace default wait for approval.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <div role="status" className="text-[12px]">
            {message && (
              <span className={approvalUrl ? "text-warning" : "text-good"}>
                {message}{" "}
                {approvalUrl && (
                  <Link href={approvalUrl} className="text-accent underline">
                    Review the approval
                  </Link>
                )}
              </span>
            )}
          </div>
          <ActionErrorText error={save.error} message={save.message} />
          {dirty && (
            <Button type="button" variant="ghost" onClick={() => setDraft(saved)}>
              Discard
            </Button>
          )}
          <Button type="submit" variant="primary" disabled={!dirty || !!spendError} pending={save.pending}>
            Save caps
          </Button>
        </div>
      </div>
    </form>
  );
}
