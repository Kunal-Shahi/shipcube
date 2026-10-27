"use client";

import { CheckCircle2, Info } from "lucide-react";
import { useState } from "react";
import { Button, Field, Input, Panel, PanelHeader, Pill, Select } from "@/components/ui";
import { cn } from "@/lib/cn";
import { date } from "@/lib/format";
import type { ProviderConfig, ProviderId } from "@/lib/types";

const MODELS: Partial<Record<ProviderId, string[]>> = { meta: ["muse-spark"] };
const MODEL_PLACEHOLDER: Partial<Record<ProviderId, string>> = {
  "openai-compatible": "accounts/fireworks/models/…",
  openai: "Model ID from your OpenAI dashboard",
  anthropic: "claude-haiku-5-5",
};

const KEY_PREFIX: Partial<Record<ProviderId, string>> = { openai: "sk-", anthropic: "sk-ant-" };

export function Providers({ initial, apps }: { initial: ProviderConfig[]; apps: { id: string; name: string; aiProvider: string }[] }) {
  const [providers, setProviders] = useState(initial);
  const [defaultId, setDefaultId] = useState<ProviderId>("meta");
  const [editing, setEditing] = useState<ProviderId | null>(null);
  const connected = providers.filter((p) => p.connected);

  return (
    <div className="flex flex-col gap-4">
      <Panel>
        <PanelHeader
          title="Default for apps"
          description="Apps that call a model through the AI gateway use this unless they override it. ShipCube's own load estimation always uses Muse."
        />
        <div className="flex flex-wrap items-end gap-3 p-4">
          <Field label="Provider" htmlFor="default-provider">
            <Select id="default-provider" value={defaultId} onChange={(e) => setDefaultId(e.target.value as ProviderId)} className="w-[260px]">
              {connected.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                  {p.id === "meta" ? " (recommended)" : ""}
                </option>
              ))}
            </Select>
          </Field>
        </div>
      </Panel>

      <Panel>
        <PanelHeader title="Providers" description="Keys are encrypted at rest, scoped to this workspace and never sent back to the browser." />
        <ul className="divide-y divide-border">
          {providers.map((p) => (
            <li key={p.id} className="px-4 py-3.5">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 max-w-[70ch]">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{p.label}</span>
                    {p.id === defaultId && <Pill tone="accent">Default</Pill>}
                    {p.connected ? (
                      <Pill tone="good" icon={<CheckCircle2 className="size-3.5" aria-hidden />}>Connected</Pill>
                    ) : (
                      <Pill>Not connected</Pill>
                    )}
                  </div>
                  <p className="mt-0.5 text-fg-muted">{p.description}</p>
                  {p.connected && p.id !== "meta" && (
                    <p className="mt-1 flex flex-wrap gap-x-4 text-[12px] text-fg-muted">
                      <span>Key <span className="font-mono">{p.keyHint}</span></span>
                      {p.baseUrl && <span className="font-mono break-all">{p.baseUrl}</span>}
                      {p.defaultModel && <span>Model <span className="font-mono">{p.defaultModel}</span></span>}
                      {p.addedAt && <span>Added {date(p.addedAt)}</span>}
                    </p>
                  )}
                  {p.id === "meta" && (
                    <p className="mt-1 text-[12px] text-fg-muted">
                      Included with ShipCube. Model <span className="font-mono">{p.defaultModel}</span>
                    </p>
                  )}
                </div>
                {p.id !== "meta" && editing !== p.id && (
                  <div className="flex gap-2">
                    <Button size="sm" onClick={() => setEditing(p.id)}>{p.connected ? "Replace key" : "Add key"}</Button>
                    {p.connected && (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          setProviders((all) => all.map((x) => (x.id === p.id ? { ...x, connected: false, keyHint: undefined } : x)));
                          if (defaultId === p.id) setDefaultId("meta");
                        }}
                      >
                        Remove
                      </Button>
                    )}
                  </div>
                )}
              </div>
              {editing === p.id && (
                <KeyForm
                  provider={p}
                  onCancel={() => setEditing(null)}
                  onSaved={(patch) => {
                    setProviders((all) => all.map((x) => (x.id === p.id ? { ...x, ...patch, connected: true, addedAt: new Date().toISOString() } : x)));
                    setEditing(null);
                  }}
                />
              )}
            </li>
          ))}
        </ul>
        <div className="flex items-start gap-2 border-t border-border px-4 py-3 text-[12px] text-fg-muted">
          <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <span>
            Claude Pro and Max subscriptions can&apos;t be used here: Anthropic doesn&apos;t allow third-party products to use
            claude.ai sign-in. Use an Anthropic API key instead. Sign in with ChatGPT is on the roadmap.
          </span>
        </div>
      </Panel>

      <Panel>
        <PanelHeader title="Per-app overrides" description="Your agent can change these with the ai_config tool." />
        <div className="overflow-x-auto">
          <table className="w-full min-w-[520px] text-left">
            <thead className="text-[12px] text-fg-muted">
              <tr className="border-b border-border">
                <th className="px-4 py-2 font-medium">App</th>
                <th className="px-4 py-2 font-medium">Provider</th>
              </tr>
            </thead>
            <tbody>
              {apps.map((a) => (
                <tr key={a.id} className="border-b border-border last:border-0">
                  <td className="px-4 py-2 font-mono text-[12.5px]">{a.name}</td>
                  <td className={cn("px-4 py-2", a.aiProvider === "None" && "text-fg-subtle")}>
                    {a.aiProvider === "None" ? "Doesn't call a model" : a.aiProvider}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}

function KeyForm({
  provider,
  onCancel,
  onSaved,
}: {
  provider: ProviderConfig;
  onCancel: () => void;
  onSaved: (p: Partial<ProviderConfig>) => void;
}) {
  const [key, setKey] = useState("");
  const [baseUrl, setBaseUrl] = useState(provider.baseUrl ?? "");
  const [model, setModel] = useState(provider.defaultModel ?? MODELS[provider.id]?.[0] ?? "");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const compatible = provider.id === "openai-compatible";
  const id = `key-${provider.id}`;

  return (
    <form
      className="mt-3 flex flex-col gap-4 rounded-md border border-border bg-surface-2 p-4"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        const k = key.trim();
        if (!k) return setError("Paste the API key.");
        if (compatible && !/^https?:\/\/.+/.test(baseUrl)) return setError("Enter the base URL, for example https://api.fireworks.ai/inference/v1.");
        setPending(true);
        setError(null);
        setTimeout(() => {
          setPending(false);
          const prefix = KEY_PREFIX[provider.id];
          if (prefix && !k.startsWith(prefix)) {
            setError(`${provider.label} rejected this key (401 Unauthorized). ${provider.label} keys start with "${prefix}". Check you copied the whole key.`);
            return;
          }
          onSaved({ keyHint: `${k.slice(0, 3)}…${k.slice(-4)}`, baseUrl: compatible ? baseUrl : undefined, defaultModel: model });
        }, 700);
      }}
    >
      {compatible && (
        <Field label="Base URL" htmlFor={`${id}-url`}>
          <Input id={`${id}-url`} value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://api.fireworks.ai/inference/v1" className="font-mono text-[12.5px]" />
        </Field>
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="API key" htmlFor={id} error={error} help="ShipCube checks the key with one small request before saving it.">
          <Input id={id} type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} className="font-mono text-[12.5px]" />
        </Field>
        <Field label="Default model" htmlFor={`${id}-model`}>
          {MODELS[provider.id] ? (
            <Select id={`${id}-model`} value={model} onChange={(e) => setModel(e.target.value)}>
              {MODELS[provider.id]!.map((m) => <option key={m}>{m}</option>)}
            </Select>
          ) : (
            <Input id={`${id}-model`} value={model} onChange={(e) => setModel(e.target.value)} placeholder={MODEL_PLACEHOLDER[provider.id]} className="font-mono text-[12.5px]" />
          )}
        </Field>
      </div>
      <div className="flex gap-2">
        <Button type="submit" variant="primary" pending={pending}>{pending ? "Checking key…" : "Test and save"}</Button>
        <Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button>
      </div>
    </form>
  );
}
