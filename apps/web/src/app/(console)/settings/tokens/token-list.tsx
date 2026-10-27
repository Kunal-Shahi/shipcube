"use client";

import { useState } from "react";
import { TokenCreator } from "@/components/token-creator";
import { Button, EmptyState, Panel, PanelHeader } from "@/components/ui";
import { ago, date } from "@/lib/format";
import type { ApiToken } from "@/lib/types";

export function TokenList({ initial }: { initial: ApiToken[] }) {
  const [tokens, setTokens] = useState(initial);
  const [confirm, setConfirm] = useState<string | null>(null);

  return (
    <div className="flex flex-col gap-4">
      <Panel>
        <PanelHeader title="Active tokens" description="Revoking a token disconnects the agent that uses it within a minute." />
        {tokens.length === 0 ? (
          <EmptyState
            title="No API tokens"
            body="Create a token below, then add ShipCube to your coding agent with it. You need one token per machine or agent."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[680px] text-left">
              <thead className="text-[12px] text-fg-muted">
                <tr className="border-b border-border">
                  <th className="px-4 py-2 font-medium">Name</th>
                  <th className="px-4 py-2 font-medium">Token</th>
                  <th className="px-4 py-2 font-medium">Permissions</th>
                  <th className="px-4 py-2 font-medium">Last used</th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody>
                {tokens.map((t) => (
                  <tr key={t.id} className="border-b border-border last:border-0">
                    <td className="px-4 py-2">
                      <div className="font-medium">{t.name}</div>
                      <div className="text-[12px] text-fg-muted">Created {date(t.createdAt)}</div>
                    </td>
                    <td className="px-4 py-2 font-mono text-[12px] text-fg-muted">{t.prefix}…</td>
                    <td className="px-4 py-2">
                      <div className="flex flex-wrap gap-1">
                        {t.scopes.map((s) => (
                          <span key={s} className="rounded bg-surface-2 px-1.5 font-mono text-[11.5px] text-fg-muted">{s}</span>
                        ))}
                      </div>
                    </td>
                    <td className="px-4 py-2 text-fg-muted">{t.lastUsedAt ? ago(t.lastUsedAt) : "Never"}</td>
                    <td className="px-4 py-2 text-right whitespace-nowrap">
                      {confirm === t.id ? (
                        <span className="inline-flex items-center gap-2">
                          <Button size="sm" variant="danger" onClick={() => { setTokens(tokens.filter((x) => x.id !== t.id)); setConfirm(null); }}>
                            Revoke token
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => setConfirm(null)}>Cancel</Button>
                        </span>
                      ) : (
                        <Button size="sm" variant="ghost" onClick={() => setConfirm(t.id)}>Revoke</Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
      <Panel>
        <PanelHeader title="Create a token" />
        <div className="p-4">
          <TokenCreator
            onCreated={(t) =>
              setTokens((all) => [
                { id: t.id, name: t.name, prefix: t.value.slice(0, 11), scopes: t.scopes, createdAt: new Date().toISOString(), lastUsedAt: null },
                ...all,
              ])
            }
          />
        </div>
      </Panel>
    </div>
  );
}
