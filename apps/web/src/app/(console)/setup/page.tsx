import type { Metadata } from "next";
import { headers } from "next/headers";
import { CheckCircle2 } from "lucide-react";
import { PageHeader, Panel, PanelHeader } from "@/components/ui";
import { cn } from "@/lib/cn";
import { TOOLS, type ToolGroup } from "@/lib/mcp";
import { supports } from "@/lib/api";
import { config } from "@/lib/server/config";
import { ConnectSteps } from "./connect-steps";

export const metadata: Metadata = { title: "Connect your agent" };

type Turn =
  | { who: "you"; text: string }
  | { who: "agent"; text: string }
  | { who: "tool"; name: string; args: string; result: string; waiting?: boolean }
  | { who: "event"; text: string };

// Apps are served on their Cloud Run run.app URLs (security, PR #2). The approval link points
// at whichever console is showing this page.
const APP_URL = "regional-revenue-tracker-123456789012.us-central1.run.app";

const conversation = (consoleUrl: string): Turn[] => [
  { who: "you", text: "Deploy this tracker to our GCP project and share it with my team. Keep it under $20 a month." },
  {
    who: "tool",
    name: "estimate_load",
    args: `{ "upload_id": "upl_9f2c", "entrypoint": "app.py" }`,
    result: "bursty-interactive (0.91), T1 Serverless, 1 vCPU / 512 MiB, about $6.80/month\nexplained_by: muse-spark. Idle 58% of the day, so scale-to-zero avoids paying for idle time.",
  },
  {
    who: "tool",
    name: "deploy_app",
    args: `{ "name": "regional-revenue-tracker", "tier": "T1", "project": "acme-analytics-prod" }`,
    result: `status: awaiting_approval\napproval_url: ${consoleUrl}/approvals/apr_7k2m`,
    waiting: true,
  },
  {
    who: "agent",
    text: `This is the first deploy to acme-analytics-prod, so it needs your approval: ${consoleUrl}/approvals/apr_7k2m. I'll continue once you approve.`,
  },
  { who: "event", text: "You approve the deploy in the console." },
  {
    who: "tool",
    name: "operation",
    args: `{ "action": "get", "id": "op_31d8" }`,
    result: `succeeded: https://${APP_URL} (private)`,
  },
  {
    who: "tool",
    name: "set_caps",
    args: `{ "app": "regional-revenue-tracker", "spend_usd_per_month": 20 }`,
    result: "Caps set. $20/month is under the workspace default, so no approval needed.",
  },
  {
    who: "tool",
    name: "set_access",
    args: `{ "app": "regional-revenue-tracker", "add": ["group:finance-team@example.com"] }`,
    result: "Access list: kunal@example.com (owner), finance-team@example.com (viewer)",
  },
  {
    who: "agent",
    text: `Done. The tracker is live at ${APP_URL} and bills to acme-analytics-prod. Only you and finance-team@ can open it, and it stops serving at $20 this month. ShipCube expects about $6.80.`,
  },
];

export default async function SetupPage({ searchParams }: { searchParams: Promise<{ welcome?: string }> }) {
  const { welcome } = await searchParams;
  const h = await headers();
  const consoleOrigin = `${h.get("x-forwarded-proto") ?? "http"}://${h.get("host") ?? "localhost:3000"}`;
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
          <ConnectSteps canCreateTokens={await supports("tokens")} mcpUrl={config().mcpUrl} />
        </Panel>

        <Panel className="self-start">
          <PanelHeader title="What it looks like" description="Prompt in Claude Code, with ShipCube's tool calls" />
          <ol className="flex flex-col gap-3 p-4">
            {conversation(consoleOrigin).map((t, i) => (
              <li key={i}>
                {t.who === "event" ? (
                  <div className="flex items-center gap-2 rounded-md border border-dashed border-border-strong px-2.5 py-2 text-[12px] text-fg-muted">
                    <CheckCircle2 className="size-3.5 text-good" aria-hidden />
                    {t.text}
                  </div>
                ) : t.who === "tool" ? (
                  <div className={cn("overflow-hidden rounded-md border", t.waiting ? "border-warning/50" : "border-border")}>
                    <div className="flex items-center gap-2 border-b border-border bg-surface-2 px-2.5 py-1.5 font-mono text-[12px]">
                      <span className="text-fg-subtle">shipcube</span>
                      <span className="font-medium">{t.name}</span>
                    </div>
                    <pre className="overflow-x-auto px-2.5 py-2 font-mono text-[11.5px] leading-[18px] whitespace-pre-wrap text-fg-muted">{t.args}</pre>
                    <pre className={cn("overflow-x-auto border-t px-2.5 py-2 font-mono text-[11.5px] leading-[18px] whitespace-pre-wrap", t.waiting ? "border-warning/50 bg-warning-soft" : "border-border")}>{t.result}</pre>
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
          description="You don't call these yourself. Ask in plain words and the agent picks the tool. A few actions wait for your approval in the console first."
        />
        <div className="overflow-x-auto">
          <table className="w-full min-w-[860px] text-left">
            <thead className="text-[12px] text-fg-muted">
              <tr className="border-b border-border">
                <th className="px-4 py-2 font-medium">Tool</th>
                <th className="px-4 py-2 font-medium">What it does</th>
                <th className="px-4 py-2 font-medium">Ask something like</th>
                <th className="px-4 py-2 font-medium">Needs your approval</th>
              </tr>
            </thead>
            {(["Apps", "Data"] as ToolGroup[]).map((g) => (
              <tbody key={g}>
                <tr className="border-b border-border bg-surface-2">
                  <th colSpan={4} className="px-4 py-1.5 text-[12px] font-medium text-fg-muted">
                    {g === "Apps" ? "Apps" : "Data (from the semantic layer)"}
                  </th>
                </tr>
                {TOOLS.filter((t) => t.group === g).map((t) => (
                  <tr key={t.name} className="border-b border-border align-top last:border-0">
                    <td className="px-4 py-2 font-mono text-[12.5px] whitespace-nowrap">{t.name}</td>
                    <td className="max-w-[56ch] px-4 py-2">{t.purpose}</td>
                    <td className="px-4 py-2 text-fg-muted">{t.prompt.startsWith("(") ? t.prompt : <>&ldquo;{t.prompt}&rdquo;</>}</td>
                    <td className="px-4 py-2 text-fg-muted">{t.approval ?? <span className="text-fg-subtle">No</span>}</td>
                  </tr>
                ))}
              </tbody>
            ))}
          </table>
        </div>
      </Panel>
    </>
  );
}
