"use client";

import { useState } from "react";
import { Button, Field, Input, Meter, Select } from "@/components/ui";
import { count, money } from "@/lib/format";
import type { Caps } from "@/lib/types";

type Usage = { spend: number; peakRpm: number; peakConcurrency: number; tokensToday: number };

export function CapsForm({ caps, usage, appName }: { caps: Caps; usage: Usage; appName: string }) {
  const [draft, setDraft] = useState(caps);
  const [saved, setSaved] = useState(caps);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);
  const spendError = draft.monthlySpend < usage.spend ? `Already spent ${money(usage.spend)} this month. Set the cap above that, or the app stops now.` : null;

  const num = (k: keyof Caps) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setDraft({ ...draft, [k]: e.target.value === "" ? 0 : Number(e.target.value) });

  const rows: { key: keyof Caps; label: string; used: number; usedLabel: string; help: string; step?: number }[] = [
    { key: "monthlySpend", label: "Monthly spend ($)", used: usage.spend, usedLabel: `${money(usage.spend)} of ${money(draft.monthlySpend)} this month`, help: "The app stops serving when it reaches this." , step: 1 },
    { key: "requestsPerMinute", label: "Requests per minute", used: usage.peakRpm, usedLabel: `Peak ${count(usage.peakRpm)} in the last 7 days`, help: "Extra requests get HTTP 429." },
    { key: "concurrency", label: "Concurrent requests per instance", used: usage.peakConcurrency, usedLabel: `Peak ${usage.peakConcurrency} in the last 7 days`, help: "Cloud Run concurrency." },
    { key: "llmTokensPerDay", label: "LLM tokens per day", used: usage.tokensToday, usedLabel: `${count(usage.tokensToday)} used today`, help: "Through the AI gateway. Resets at 00:00 UTC." },
  ];

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (spendError) return;
        setSaving(true);
        setTimeout(() => {
          setSaving(false);
          setSaved(draft);
          setMessage(`Caps saved for ${appName}`);
          setTimeout(() => setMessage(null), 3000);
        }, 500);
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
              value={draft[r.key] as number}
              onChange={num(r.key)}
            />
            <div className="mt-1 flex flex-col gap-1">
              <Meter value={r.used} max={draft[r.key] as number} label={r.label} />
              <span className="num text-[12px] text-fg-muted">{r.usedLabel}</span>
            </div>
          </Field>
        ))}
        <Field label="Max instances" htmlFor="cap-maxInstances" help="Upper bound for autoscaling.">
          <Input id="cap-maxInstances" type="number" min={1} className="num" value={draft.maxInstances} onChange={num("maxInstances")} />
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
          Your agent can change these too, with the <code className="font-mono">set_caps</code> tool.
        </p>
        <div className="flex items-center gap-3">
          <span role="status" className="text-[12px] text-good">{message}</span>
          {dirty && (
            <Button type="button" variant="ghost" onClick={() => setDraft(saved)}>
              Discard
            </Button>
          )}
          <Button type="submit" variant="primary" disabled={!dirty || !!spendError} pending={saving}>
            Save caps
          </Button>
        </div>
      </div>
    </form>
  );
}
