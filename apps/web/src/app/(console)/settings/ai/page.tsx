import type { Metadata } from "next";
import { notConnected } from "@/components/not-connected";
import { PageHeader } from "@/components/ui";
import { listApps, listProviders } from "@/lib/api";
import { Providers } from "./providers";

export const metadata: Metadata = { title: "AI providers" };

export default async function AiPage() {
  const blocked = await notConnected("providers");
  const [providers, apps] = blocked ? [[], []] : await Promise.all([listProviders(), listApps()]);
  return (
    <>
      <PageHeader
        title="AI providers"
        description="Models your apps can call through ShipCube's AI gateway. Muse is the default; bring your own key for anything else."
      />
      {blocked ?? <Providers initial={providers} apps={apps.filter((a) => !a.deletedAt).map(({ id, name, aiProvider }) => ({ id, name, aiProvider }))} />}
    </>
  );
}
