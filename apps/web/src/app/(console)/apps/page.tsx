import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { EmptyState, HealthPill, PageHeader, Panel, TableSkeleton, TierBadge, ButtonLink } from "@/components/ui";
import { CodeBlock } from "@/components/copy-button";
import { demoState, listApps, type DemoState } from "@/lib/api";
import { ago, count, money } from "@/lib/format";
import type { AppKind } from "@/lib/types";

export const metadata: Metadata = { title: "Apps" };

const KIND_LABEL: Record<AppKind, string> = { tracker: "Tracker", dashboard: "Dashboard", report: "Report", api: "API", job: "Job" };

type Props = { searchParams: Promise<Record<string, string | string[] | undefined>> };

export default async function AppsPage({ searchParams }: Props) {
  const state = demoState(await searchParams);
  return (
    <>
      <PageHeader
        title="Apps"
        description="Everything your agents have deployed through ShipCube. Apps are private until you share them."
        actions={
          <ButtonLink href="/setup" variant="secondary">
            Connect an agent
          </ButtonLink>
        }
      />
      <Suspense key={state} fallback={<Panel><TableSkeleton rows={6} cols={6} /></Panel>}>
        <AppsTable state={state} />
      </Suspense>
    </>
  );
}

async function AppsTable({ state }: { state: DemoState }) {
  const apps = await listApps(state);

  if (apps.length === 0) {
    return (
      <Panel>
        <EmptyState
          title="No apps yet"
          body={
            <>
              <p>
                Apps appear here after your coding agent deploys them. Open your project in Claude Code, Cursor or Muse
                Code and ask it to deploy, for example:
              </p>
              <div className="mt-3 max-w-[560px]">
                <CodeBlock label="Prompt" wrap code="Deploy this tracker with ShipCube and share it with finance-team@acme-analytics.com" />
              </div>
            </>
          }
          action={<ButtonLink href="/setup" variant="primary">Connect your agent</ButtonLink>}
        />
      </Panel>
    );
  }

  const attention = apps.filter((a) => a.health === "down" || a.health === "degraded").length;

  return (
    <Panel>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-border px-4 py-2.5 text-[12px] text-fg-muted">
        <span className="num">{apps.length} apps</span>
        {attention > 0 && <span className="num text-warning">{attention} need attention</span>}
        <span className="num">
          {money(apps.reduce((s, a) => s + a.costMonthToDate, 0))} this month
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[820px] text-left">
          <thead className="text-[12px] text-fg-muted">
            <tr className="border-b border-border">
              <th className="px-4 py-2 font-medium">Name</th>
              <th className="px-4 py-2 font-medium">Status</th>
              <th className="px-4 py-2 font-medium">Tier</th>
              <th className="px-4 py-2 text-right font-medium">Requests, 24 h</th>
              <th className="px-4 py-2 text-right font-medium">Spend / cap</th>
              <th className="px-4 py-2 text-right font-medium">Shared with</th>
              <th className="px-4 py-2 font-medium">Last deploy</th>
            </tr>
          </thead>
          <tbody>
            {apps.map((a) => (
              <tr key={a.id} className="group relative border-b border-border last:border-0 hover:bg-surface-2">
                <td className="px-4 py-2">
                  <Link href={`/apps/${a.id}`} className="font-medium after:absolute after:inset-0 focus-visible:outline-none group-has-[:focus-visible]:underline">
                    {a.name}
                  </Link>
                  <div className="text-[12px] text-fg-muted">
                    {KIND_LABEL[a.kind]} · {a.billing === "own-gcp" ? "Your GCP" : "ShipCube GCP"}
                  </div>
                </td>
                <td className="px-4 py-2"><HealthPill health={a.health} /></td>
                <td className="px-4 py-2"><TierBadge tier={a.tier} long /></td>
                <td className="num px-4 py-2 text-right">{count(a.requests24h)}</td>
                <td className="num px-4 py-2 text-right">
                  {money(a.costMonthToDate)} <span className="text-fg-subtle">/ {money(a.spendCap)}</span>
                </td>
                <td className="num px-4 py-2 text-right">{a.sharedWith}</td>
                <td className="px-4 py-2 text-fg-muted">
                  {ago(a.lastDeployAt)} <span className="text-fg-subtle">· {a.deployedBy}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
