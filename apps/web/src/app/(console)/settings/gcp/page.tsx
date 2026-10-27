import type { Metadata } from "next";
import { PageHeader, Panel, PanelHeader } from "@/components/ui";
import { demoState, getBilling } from "@/lib/api";
import { money } from "@/lib/format";
import { GcpSettings } from "./gcp-settings";

export const metadata: Metadata = { title: "GCP project" };

export default async function GcpPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const billing = await getBilling(demoState(await searchParams));
  return (
    <>
      <PageHeader
        title="GCP project"
        description="Choose where new apps run. Existing apps stay where they are until you redeploy them."
      />
      <Panel>
        <PanelHeader title="Default for new apps" />
        <div className="p-4">
          <GcpSettings link={billing.gcp} creditsLeft={money(billing.credits.granted - billing.credits.used)} />
        </div>
      </Panel>
    </>
  );
}
