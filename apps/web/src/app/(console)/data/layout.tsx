import { RouteTabs } from "@/components/tabs";
import { PageHeader } from "@/components/ui";
import { listMigrations } from "@/lib/api";
import { notConnected } from "@/components/not-connected";

export default async function DataLayout({ children }: { children: React.ReactNode }) {
  const blocked = await notConnected("data");
  const proposals = blocked ? 0 : (await listMigrations()).filter((m) => m.status === "proposed").length;
  return (
    <>
      <PageHeader
        title="Data"
        description="The data model your apps and agents query: cubes with business meaning on top of your warehouse. Agents can read it and propose changes; a person reviews and applies every change here."
      />
      <RouteTabs
        tabs={[
          { href: "/data", label: "Cubes", also: "/data/cubes" },
          { href: "/data/migrations", label: "Migrations", count: proposals || undefined },
          { href: "/data/warehouse", label: "Warehouse" },
        ]}
      />
      <div className="pt-6">{blocked ?? children}</div>
    </>
  );
}
