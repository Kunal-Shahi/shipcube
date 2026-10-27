import { notFound } from "next/navigation";
import { CodeBlock } from "@/components/copy-button";
import { Panel, PanelHeader } from "@/components/ui";
import { demoState, getApp } from "@/lib/api";
import { requireSession } from "@/lib/auth";
import { AccessManager } from "./access-manager";

type Props = {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export default async function AccessPage({ params, searchParams }: Props) {
  const [{ id }, sp, session] = await Promise.all([params, searchParams, requireSession()]);
  const app = await getApp(id, demoState(sp));
  if (!app) notFound();

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
      <Panel>
        <PanelHeader
          title="Who can open this app"
          description="Apps are private by default. Access is checked with Google sign-in on every visit."
        />
        <AccessManager appId={app.id} initial={app.access} owner={session.user.email} />
      </Panel>
      <Panel className="self-start">
        <PanelHeader title="From your agent" />
        <div className="flex flex-col gap-3 p-4">
          <p className="text-fg-muted">Ask your agent in plain words; it calls the <code className="font-mono text-[12px]">set_access</code> tool:</p>
          <CodeBlock label="Prompt" wrap code={`Share ${app.name} with finance-team@acme-analytics.com, view only`} />
          <p className="text-fg-muted">Or call the tool directly:</p>
          <CodeBlock
            label="set_access"
            code={JSON.stringify({ app: app.name, add: ["group:finance-team@acme-analytics.com"], role: "viewer" }, null, 2)}
          />
          <p className="text-[12px] text-fg-muted">
            Adding a whole domain (<code className="font-mono">domain:acme.com</code>) waits for your approval under Approvals.
            People you add here directly don&apos;t need approval.
          </p>
        </div>
      </Panel>
    </div>
  );
}
