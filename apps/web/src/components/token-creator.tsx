"use client";

import { AlertTriangle } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/cn";
import { SCOPES } from "@/lib/mcp";
import { CodeBlock } from "./copy-button";
import { Button, Field, Input } from "./ui";

function generateToken() {
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return "sc_pat_" + Array.from(bytes, (b) => alphabet[b % alphabet.length]).join("");
}

export type CreatedToken = { id: string; name: string; value: string; scopes: string[] };

export function TokenCreator({
  onCreated,
  defaultName = "",
  compact,
}: {
  onCreated?: (t: CreatedToken) => void;
  defaultName?: string;
  compact?: boolean;
}) {
  const [name, setName] = useState(defaultName);
  const [scopes, setScopes] = useState<string[]>(SCOPES.map((s) => s.id));
  const [pending, setPending] = useState(false);
  const [created, setCreated] = useState<CreatedToken | null>(null);
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
        <CodeBlock label={`Token · ${created.name}`} code={created.value} />
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
      onSubmit={(e) => {
        e.preventDefault();
        setTouched(true);
        if (!name.trim() || scopes.length === 0) return;
        setPending(true);
        setTimeout(() => {
          const t = { id: crypto.randomUUID(), name: name.trim(), value: generateToken(), scopes };
          setCreated(t);
          setPending(false);
          onCreated?.(t);
        }, 450);
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
      <div>
        <Button type="submit" variant="primary" pending={pending} disabled={!!scopeError}>
          Create token
        </Button>
      </div>
    </form>
  );
}
