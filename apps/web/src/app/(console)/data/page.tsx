import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { CodeBlock } from "@/components/copy-button";
import { ButtonLink, EmptyState, Panel, Pill, TableSkeleton } from "@/components/ui";
import { demoState, getWarehouse, listCubes, type DemoState } from "@/lib/api";
import { ago } from "@/lib/format";

export const metadata: Metadata = { title: "Data model" };

type Props = { searchParams: Promise<Record<string, string | string[] | undefined>> };

export default async function DataPage({ searchParams }: Props) {
  const state = demoState(await searchParams);
  return (
    <Suspense key={state} fallback={<Panel><TableSkeleton rows={6} cols={5} /></Panel>}>
      <Cubes state={state} />
    </Suspense>
  );
}

async function Cubes({ state }: { state: DemoState }) {
  const [wh, cubes] = await Promise.all([getWarehouse(state), listCubes(state)]);

  if (!wh.connected) {
    return (
      <Panel>
        <EmptyState
          title="Connect a warehouse first"
          body="Cubes describe tables in your warehouse. Connect BigQuery, Postgres or ClickHouse with read-only credentials. You enter them here, never through an agent."
          action={<ButtonLink href="/data/warehouse" variant="primary">Connect a warehouse</ButtonLink>}
        />
      </Panel>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-fg-muted">
        <span>
          <Link href="/data/warehouse" className="text-fg hover:text-accent">{wh.name}</Link> ({wh.kind === "bigquery" ? "BigQuery" : wh.kind})
        </span>
        <Pill tone="good">Connected</Pill>
        <span className="num">Model version {wh.modelVersion}</span>
        {wh.lastCheckedAt && <span>Checked {ago(wh.lastCheckedAt)}</span>}
      </div>

      {cubes.length === 0 ? (
        <Panel>
          <EmptyState
            title="No cubes yet"
            body={
              <>
                <p>Ask your agent to model the tables your app needs. It proposes a migration, and you review and apply it under Migrations.</p>
                <div className="mt-3 max-w-[560px]">
                  <CodeBlock label="Prompt" wrap code="Look at the orders and customers tables and propose cubes for them in ShipCube" />
                </div>
              </>
            }
          />
        </Panel>
      ) : (
        <Panel>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left">
              <thead className="text-[12px] text-fg-muted">
                <tr className="border-b border-border">
                  <th className="px-4 py-2 font-medium">Cube</th>
                  <th className="px-4 py-2 font-medium">Reads from</th>
                  <th className="px-4 py-2 text-right font-medium">Dimensions</th>
                  <th className="px-4 py-2 text-right font-medium">Measures</th>
                  <th className="px-4 py-2 font-medium">Joins to</th>
                  <th className="px-4 py-2 font-medium whitespace-nowrap">Used by</th>
                </tr>
              </thead>
              <tbody>
                {cubes.map((c) => (
                  <tr key={c.name} className="relative border-b border-border align-top last:border-0 hover:bg-surface-2">
                    <td className="px-4 py-2.5">
                      <Link href={`/data/cubes/${c.name}`} className="font-mono text-[12.5px] font-medium after:absolute after:inset-0">{c.name}</Link>
                      <div className="max-w-[44ch] text-[12px] text-fg-muted">{c.description}</div>
                    </td>
                    <td className="px-4 py-2.5 font-mono text-[12px] text-fg-muted">{c.schema}.{c.table}</td>
                    <td className="num px-4 py-2.5 text-right">{c.dimensions.length}</td>
                    <td className="num px-4 py-2.5 text-right">{c.measures.length}</td>
                    <td className="px-4 py-2.5 font-mono text-[12px] text-fg-muted">{c.joins.map((j) => j.to).join(", ") || "None"}</td>
                    <td className="px-4 py-2.5 text-[12px] whitespace-nowrap text-fg-muted">{c.usedBy.length} {c.usedBy.length === 1 ? "app" : "apps"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </div>
  );
}
