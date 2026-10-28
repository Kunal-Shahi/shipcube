"use client";

import { AlertTriangle } from "lucide-react";
import { useState } from "react";
import { createTokenAction } from "@/app/(console)/actions";
import { cn } from "@/lib/cn";
import { SCOPES } from "@/lib/mcp";
import type { ApiToken } from "@/lib/types";
import { ActionErrorText } from "./action-error";
import { CodeBlock } from "./copy-button";
import { Button, Field, Input, Select } from "./ui";
import { useAction } from "./use-action";

/** The token row plus its secret, which the API returns exactly once. */
export type CreatedToken = { token: ApiToken; secret: string };

export function TokenCreator({
  onCreated,
  compact,
}: {
  onCreated?: (t: CreatedToken) => void;
  compact?: boolean;
}) {
  // Empty, with the example as placeholder and help text: a prefilled example gets typed onto.
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>(SCOPES.map((s) => s.id));
  const [days, setDays] = useState(30);
  const [created, setCreated] = useState<CreatedToken | null>(null);
  const create = useAction(createTokenAction);
  const [touched, setTouched] = useState(false);

  const nameError = touched && !name.trim() ? "Name the token after the machine or agent that uses it, so you know which one to revoke." : null;
  const scopeError = scopes.length === 0 ? "Pick at least one permission." : null;

  if (created) {
    return (
      <div className="flex flex-col gap-3">
        <div className="flex items-start gap-2 rounded-md border border-warning/40 bg-warning-soft px-3 py-2 text-[12px]">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warning" aria-hidden />
          <span>
            Copy this token now. ShipCube stores only a hash, so it can&apos;t show it again. Treat it like a password and
            keep it out of your repo.
          </span>
        </div>
        <CodeBlock label={`Token: ${created.token.name}`} code={created.secret} />
        <div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setCreated(null);
              setName("");
              setTouched(false);
            }}
          >
            Create another token
          </Button>
        </div>
      </div>
    );
  }

  return (
    <form
      noValidate
      className="flex flex-col gap-4"
      onSubmit={async (e) => {
        e.preventDefault();
        setTouched(true);
        if (!name.trim() || scopes.length === 0) return;
        const t = await create.run({ name: name.trim(), scopes, days });
        if (t) {
          setCreated(t);
          onCreated?.(t);
        }
      }}
    >
      <Field label="Token name" htmlFor="token-name" error={nameError} help="For example: MacBook – Claude Code">
        <Input
          id="token-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="MacBook – Claude Code"
          className={cn(compact ? "max-w-[360px]" : "max-w-[420px]", nameError && "border-critical")}
          autoComplete="off"
        />
      </Field>
      <Field label="Expires after" htmlFor="token-days" help="Agents stop connecting when the token expires. Create a new one then.">
        <Select id="token-days" value={days} onChange={(e) => setDays(Number(e.target.value))} className="max-w-[160px]">
          <option value={7}>7 days</option>
          <option value={30}>30 days</option>
          <option value={90}>90 days</option>
        </Select>
      </Field>
      <fieldset className="flex flex-col gap-1.5">
        <legend className="mb-1.5 text-[12px] font-medium">What the agent can do</legend>
        {SCOPES.map((s) => (
          <label key={s.id} className="flex cursor-pointer items-start gap-2.5">
            <input
              type="checkbox"
              className="mt-[3px] size-3.5 accent-[var(--accent)]"
              checked={scopes.includes(s.id)}
              onChange={(e) => setScopes(e.target.checked ? [...scopes, s.id] : scopes.filter((x) => x !== s.id))}
            />
            <span>
              {s.label} <span className="font-mono text-[12px] text-fg-subtle">{s.detail}</span>
            </span>
          </label>
        ))}
        {scopeError && <p className="text-[12px] text-critical">{scopeError}</p>}
      </fieldset>
      <ActionErrorText error={create.error} message={create.message} />
      <div>
        <Button type="submit" variant="primary" pending={create.pending} disabled={!!scopeError}>
          Create token
        </Button>
      </div>
    </form>
  );
}
