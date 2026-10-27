import type { Metadata } from "next";
import { CheckCircle2 } from "lucide-react";
import { PageHeader, Panel, PanelHeader } from "@/components/ui";
import { TOOLS } from "@/lib/mcp";
import { ConnectSteps } from "./connect-steps";

export const metadata: Metadata = { title: "Connect your agent" };

type Turn =
  | { who: "you"; text: string }
  | { who: "agent"; text: string }
  | { who: "tool"; name: string; args: string; result: string };

const CONVERSATION: Turn[] = [
  { who: "you", text: "Deploy this tracker and share it with my team. Keep it under $20 a month." },
  { who: "agent", text: "I'll estimate the load first so we pick the right tier." },
  {
    who: "tool",
    name: "estimate_load",
    args: `{ "path": "./", "entrypoint": "app.py", "sample": "olist_orders" }`,
    result: "bursty-interactive (0.91) → T1 Serverless · 1 vCPU / 512 MiB · 0–4 instances · ~$6.80/month\nIdle 58% of the day; scale-to-zero avoids paying for idle time.",
  },
  {
    who: "tool",
    name: "deploy_app",
    args: `{ "name": "regional-revenue-tracker", "tier": "T1", "project": "acme-analytics-prod" }`,
    result: "Deployed revision 00007 · https://regional-revenue-tracker.apps.shipcube.dev · private",
  },
  {
    who: "tool",
    name: "set_caps",
    args: `{ "app": "regional-revenue-tracker", "monthly_spend": 20, "requests_per_minute": 600 }`,
    result: "Caps set · spend $20.00/month · 600 req/min",
  },
  {
    who: "tool",
    name: "grant_access",
    args: `{ "app": "regional-revenue-tracker", "principal": "finance-team@acme-analytics.com", "kind": "group", "role": "viewer" }`,
    result: "finance-team@acme-analytics.com can view",
  },
  {
    who: "agent",
    text: "Done. The tracker is live at regional-revenue-tracker.apps.shipcube.dev. Only you and finance-team@ can open it, and it stops serving if it reaches $20 this month. ShipCube expects about $6.80.",
  },
];

export default async function SetupPage({ searchParams }: { searchParams: Promise<{ welcome?: string }> }) {
  const { welcome } = await searchParams;
  return (
    <>
      {welcome && (
        <div className="mb-6 flex items-start gap-2.5 rounded-lg border border-border bg-surface px-4 py-3">
          <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-good" aria-hidden />
          <p>
            <span className="font-medium">Your account is ready.</span>{" "}
            <span className="text-fg-muted">Connect your coding agent below, then ask it to deploy something.</span>
          </p>
        </div>
      )}
      <PageHeader
        title="Connect your agent"
        description="ShipCube is an MCP server. Add it to Claude Code, Cursor or Muse Code once, and your agent can estimate, deploy, cap, share and monitor apps for you. This console is where you check and change what it did."
      />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
        <Panel className="p-5">
          <ConnectSteps />
        </Panel>

        <Panel className="self-start">
          <PanelHeader title="What it looks like" description="Prompt in Claude Code, with ShipCube's tool calls" />
          <ol className="flex flex-col gap-3 p-4">
            {CONVERSATION.map((t, i) => (
              <li key={i}>
                {t.who === "tool" ? (
                  <div className="overflow-hidden rounded-md border border-border">
                    <div className="flex items-center gap-2 border-b border-border bg-surface-2 px-2.5 py-1.5 font-mono text-[12px]">
                      <span className="text-fg-subtle">shipcube</span>
                      <span className="font-medium">{t.name}</span>
                    </div>
                    <pre className="overflow-x-auto px-2.5 py-2 font-mono text-[11.5px] leading-[18px] whitespace-pre-wrap text-fg-muted">{t.args}</pre>
                    <pre className="overflow-x-auto border-t border-border px-2.5 py-2 font-mono text-[11.5px] leading-[18px] whitespace-pre-wrap">{t.result}</pre>
                  </div>
                ) : (
                  <div>
                    <div className="text-[12px] font-medium text-fg-muted">{t.who === "you" ? "You" : "Agent"}</div>
                    <p className={t.who === "you" ? "font-medium" : undefined}>{t.text}</p>
                  </div>
                )}
              </li>
            ))}
          </ol>
        </Panel>
      </div>

      <Panel className="mt-6">
        <PanelHeader
          title="Tools your agent gets"
          description="You don't call these yourself. Ask in plain words and the agent picks the tool."
        />
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-left">
            <thead className="text-[12px] text-fg-muted">
              <tr className="border-b border-border">
                <th className="px-4 py-2 font-medium">Tool</th>
                <th className="px-4 py-2 font-medium">What it does</th>
                <th className="px-4 py-2 font-medium">Ask something like</th>
              </tr>
            </thead>
            <tbody>
              {TOOLS.map((t) => (
                <tr key={t.name} className="border-b border-border align-top last:border-0">
                  <td className="px-4 py-2 font-mono text-[12.5px] whitespace-nowrap">{t.name}</td>
                  <td className="max-w-[60ch] px-4 py-2">{t.purpose}</td>
                  <td className="px-4 py-2 text-fg-muted">&ldquo;{t.prompt}&rdquo;</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </>
  );
}
