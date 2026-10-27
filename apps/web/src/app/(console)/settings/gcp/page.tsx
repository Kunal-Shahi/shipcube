import type { Metadata } from "next";
import { PageHeader, Panel, PanelHeader } from "@/components/ui";
import { demoState, deployerAccount, getBilling, supports } from "@/lib/api";
import { money } from "@/lib/format";
import { GcpSettings } from "./gcp-settings";

export const metadata: Metadata = { title: "GCP project" };

export default async function GcpPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const state = demoState(await searchParams);
  const [canLink, canBill, deployer] = await Promise.all([supports("gcp_link"), supports("billing"), deployerAccount()]);
  const billing = canBill ? await getBilling(state) : null;
  return (
    <>
      <PageHeader
        title="GCP project"
        description="Choose where new apps run. Existing apps stay where they are until you redeploy them."
      />
      <Panel>
        <PanelHeader title="Default for new apps" />
        <div className="p-4">
          <GcpSettings
            link={billing?.gcp ?? { linked: false }}
            deployer={deployer}
            canLink={canLink}
            creditsLeft={billing ? money(billing.credits.granted - billing.credits.used) : undefined}
          />
        </div>
      </Panel>
    </>
  );
}
