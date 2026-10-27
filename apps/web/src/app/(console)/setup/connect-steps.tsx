"use client";

import { useState } from "react";
import { CodeBlock } from "@/components/copy-button";
import { TokenCreator } from "@/components/token-creator";
import { cn } from "@/lib/cn";
import { MCP_URL, TOKEN_ENV, snippets } from "@/lib/mcp";

const CLIENTS = [
  { id: "claude", label: "Claude Code" },
  { id: "cursor", label: "Cursor" },
  { id: "other", label: "Muse Code and other clients" },
] as const;

type ClientId = (typeof CLIENTS)[number]["id"];

function Step({ n, title, children, done }: { n: number; title: string; children: React.ReactNode; done?: boolean }) {
  return (
    <li className="relative grid grid-cols-[28px_minmax(0,1fr)] gap-x-4 pb-8 last:pb-0">
      <span
        className={cn(
          "num z-10 grid size-7 place-items-center rounded-full border bg-surface text-[12px] font-medium",
          done ? "border-good text-good" : "border-border-strong text-fg-muted",
        )}
        aria-hidden
      >
        {n}
      </span>
      <span className="absolute top-7 bottom-0 left-[13.5px] w-px bg-border" aria-hidden />
      <div className="min-w-0 pt-1">
        <h2 className="text-[14px] font-semibold leading-5">{title}</h2>
        <div className="mt-3">{children}</div>
      </div>
    </li>
  );
}

export function ConnectSteps({ canCreateTokens }: { canCreateTokens: boolean }) {
  const [token, setToken] = useState<string | null>(null);
  const [client, setClient] = useState<ClientId>("claude");
  const s = snippets();

  return (
    <ol className="flex flex-col">
      <Step n={1} title="Create an API token" done={!!token}>
        <p className="mb-4 max-w-[68ch] text-fg-muted">
          The token lets your agent act as you. Create one per machine or agent so you can revoke them separately. All
          tokens are listed under <a href="/settings/tokens" className="text-accent hover:underline">API tokens</a>.
        </p>
        {canCreateTokens ? (
          <TokenCreator defaultName="MacBook – Claude Code" compact onCreated={(t) => setToken(t.secret)} />
        ) : (
          <p className="rounded-md border border-border bg-surface-2 px-3 py-2 text-fg-muted">
            Token creation isn&apos;t available in this deployment yet (it needs <code className="font-mono text-[12px]">POST /v1/tokens</code> on
            the API). Ask a ShipCube operator for a token.
          </p>
        )}
      </Step>

      <Step n={2} title="Put the token in your shell environment">
        <p className="mb-3 max-w-[68ch] text-fg-muted">
          Add this to <code className="font-mono text-[12px]">~/.zshrc</code> or your secrets manager. The snippets below
          read <code className="font-mono text-[12px]">${TOKEN_ENV}</code>, so the token never lands in a config file you might commit.
        </p>
        <CodeBlock label="Shell" code={snippets(token ?? undefined).env} />
      </Step>

      <Step n={3} title="Add ShipCube to your agent">
        <div role="tablist" aria-label="Coding agent" className="-mb-px flex gap-5 overflow-x-auto border-b border-border">
          {CLIENTS.map((c) => (
            <button
              key={c.id}
              role="tab"
              type="button"
              aria-selected={client === c.id}
              onClick={() => setClient(c.id)}
              className={cn(
                "h-9 shrink-0 border-b-2 text-[13px]",
                client === c.id ? "border-accent font-medium text-fg" : "border-transparent text-fg-muted hover:text-fg",
              )}
            >
              {c.label}
            </button>
          ))}
        </div>
        <div role="tabpanel" className="flex flex-col gap-3 pt-4">
          {client === "claude" && (
            <>
              <p className="text-fg-muted">Run this once. <code className="font-mono text-[12px]">--scope user</code> makes ShipCube available in every project.</p>
              <CodeBlock label="Terminal" code={s.claudeCode} />
              <p className="text-fg-muted">Check that it connected. ShipCube should show as <span className="text-good">✓ Connected</span>:</p>
              <CodeBlock label="Terminal" code={s.claudeVerify} />
              <p className="text-fg-muted">
                To share the setup with your team instead, commit this as <code className="font-mono text-[12px]">.mcp.json</code> in the
                repo. Each person keeps their own token in <code className="font-mono text-[12px]">${TOKEN_ENV}</code>.
              </p>
              <CodeBlock label=".mcp.json" code={s.claudeProject} />
            </>
          )}
          {client === "cursor" && (
            <>
              <p className="text-fg-muted">
                Add this to <code className="font-mono text-[12px]">~/.cursor/mcp.json</code> for every project, or{" "}
                <code className="font-mono text-[12px]">.cursor/mcp.json</code> for one. Then open Cursor Settings → MCP and
                check that <span className="font-mono text-[12px]">shipcube</span> has a green dot.
              </p>
              <CodeBlock label="~/.cursor/mcp.json" code={s.cursor} />
            </>
          )}
          {client === "other" && (
            <>
              <p className="max-w-[72ch] text-fg-muted">
                ShipCube speaks MCP over Streamable HTTP at <code className="font-mono text-[12px]">{MCP_URL}</code> with a
                bearer token. For Muse Code and any client that accepts a remote server, add:
              </p>
              <CodeBlock label="MCP config (JSON)" code={s.generic} />
              <p className="text-fg-muted">
                If your client only runs local (stdio) servers, bridge with{" "}
                <code className="font-mono text-[12px]">mcp-remote</code>:
              </p>
              <CodeBlock label="MCP config (stdio)" code={s.stdio} />
            </>
          )}
        </div>
      </Step>

      <Step n={4} title="Try it">
        <p className="mb-3 text-fg-muted">Open any project and ask your agent:</p>
        <CodeBlock label="Prompt" wrap code="Which ShipCube apps do I have, and are they healthy?" />
        <p className="mt-3 text-fg-muted">It should call <code className="font-mono text-[12px]">list_apps</code> and list your apps. If it says the server isn&apos;t available, restart the agent so it reloads its MCP config.</p>
      </Step>
    </ol>
  );
}
