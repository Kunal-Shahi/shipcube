import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { KindPill, MigrationStatusPill } from "@/components/migration-meta";
import { EmptyState, Panel, PanelHeader, TableSkeleton } from "@/components/ui";
import { demoState, listMigrations, type DemoState } from "@/lib/api";
import { ago, date } from "@/lib/format";
import type { Migration } from "@/lib/types";

export const metadata: Metadata = { title: "Migrations" };

type Props = { searchParams: Promise<Record<string, string | string[] | undefined>> };

export default async function MigrationsPage({ searchParams }: Props) {
  const state = demoState(await searchParams);
  return (
    <Suspense key={state} fallback={<Panel><TableSkeleton rows={6} cols={4} /></Panel>}>
      <Lists state={state} />
    </Suspense>
  );
}

function impactSummary(m: Migration) {
  const i = m.impact;
  if (i.breaks.length) return <span className="text-critical">Breaks {i.breaks.length} saved {i.breaks.length === 1 ? "query" : "queries"} in {i.appsBroken.join(", ")}</span>;
  if (i.changes.length) return <span className="text-warning">Changes numbers in {i.changes.length} saved queries</span>;
  if (i.warnings.length) return <span>{i.warnings.length} queries get a rename warning</span>;
  return <span className="text-fg-muted">No saved query affected</span>;
}

async function Lists({ state }: { state: DemoState }) {
  const all = await listMigrations(state);
  const proposed = all.filter((m) => m.status === "proposed");
  const history = all.filter((m) => m.status !== "proposed");

  return (
    <div className="flex flex-col gap-4">
      <Panel>
        <PanelHeader
          title="Proposed changes"
          description="Agents propose changes with data_propose_migration. Each one was checked against every saved query before it got here."
        />
        {proposed.length === 0 ? (
          <EmptyState
            title="No changes to review"
            body="When an agent proposes a change to the data model, it shows up here with its diff and the saved queries it would affect."
          />
        ) : (
          <ul className="divide-y divide-border">
            {proposed.map((m) => (
              <li key={m.id} className="relative flex flex-wrap items-start justify-between gap-x-4 gap-y-1.5 px-4 py-3 hover:bg-surface-2">
                <div className="min-w-0 flex-1">
                  <Link href={`/data/migrations/${m.id}`} className="font-mono text-[12.5px] font-medium after:absolute after:inset-0">
                    {m.id}
                  </Link>
                  <p className="max-w-[72ch]">{m.description}</p>
                  <p className="mt-0.5 text-[12px]">
                    {impactSummary(m)}
                    <span className="text-fg-muted">. Proposed by {m.proposedBy.agent} {ago(m.proposedAt)}{m.draftedBy ? `, drafted by ${m.draftedBy}` : ""}.</span>
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <KindPill kind={m.kind} />
                  <MigrationStatusPill status={m.status} />
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>
      <Panel>
        <PanelHeader title="Applied" description="Each applied migration made one model version. Queries can pin a version while apps catch up." />
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left">
            <thead className="text-[12px] text-fg-muted">
              <tr className="border-b border-border">
                <th className="px-4 py-2 font-medium">Version</th>
                <th className="px-4 py-2 font-medium">Migration</th>
                <th className="px-4 py-2 font-medium">Kind</th>
                <th className="px-4 py-2 font-medium">Applied</th>
              </tr>
            </thead>
            <tbody>
              {history.map((m) => (
                <tr key={m.id} className="border-b border-border align-top last:border-0">
                  <td className="num px-4 py-2 font-mono text-[12.5px]">v{m.applied?.version}</td>
                  <td className="px-4 py-2">
                    <div className="font-mono text-[12.5px]">{m.id}</div>
                    <div className="text-[12px] text-fg-muted">{m.description}</div>
                  </td>
                  <td className="px-4 py-2"><KindPill kind={m.kind} /></td>
                  <td className="px-4 py-2 text-fg-muted">{m.applied && date(m.applied.at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
