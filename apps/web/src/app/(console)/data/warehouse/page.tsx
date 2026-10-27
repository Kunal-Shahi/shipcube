import type { Metadata } from "next";
import { notConnected } from "@/components/not-connected";
import { Panel, PanelHeader } from "@/components/ui";
import { demoState, getWarehouse } from "@/lib/api";
import { WarehouseForm } from "./warehouse-form";

export const metadata: Metadata = { title: "Warehouse" };

export default async function WarehousePage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const blocked = await notConnected("data");
  if (blocked) return null;
  const wh = await getWarehouse(demoState(await searchParams));
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
      <Panel>
        <PanelHeader
          title={wh.connected ? "Connected warehouse" : "Connect a warehouse"}
          description="One warehouse per workspace. Credentials are entered here by a person and never pass through an agent."
        />
        <WarehouseForm initial={wh} />
      </Panel>
      <Panel className="self-start">
        <PanelHeader title="How agents use it" />
        <div className="flex flex-col gap-2 p-4 text-fg-muted">
          <p>Agents never get warehouse credentials or raw table access. They work through the data model:</p>
          <ul className="flex list-disc flex-col gap-1 pl-5">
            <li><code className="font-mono text-[12px] text-fg">data_list_cubes</code> and <code className="font-mono text-[12px] text-fg">data_describe_cube</code> to learn what exists</li>
            <li><code className="font-mono text-[12px] text-fg">data_query</code> for typed queries, with your row rules applied</li>
            <li><code className="font-mono text-[12px] text-fg">data_propose_migration</code> to suggest model changes you review under Migrations</li>
          </ul>
        </div>
      </Panel>
    </div>
  );
}
