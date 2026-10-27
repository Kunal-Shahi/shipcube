import type { Metadata } from "next";
import { notConnected } from "@/components/not-connected";
import { Lock } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { DescList, Panel, PanelHeader, Pill } from "@/components/ui";
import { getCube } from "@/lib/api";
import type { CubeMember } from "@/lib/types";

type Props = { params: Promise<{ name: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  return { title: `${(await params).name} cube` };
}

function Members({ title, members }: { title: string; members: CubeMember[] }) {
  return (
    <Panel>
      <PanelHeader title={title} />
      {members.length === 0 ? (
        <p className="px-4 py-3 text-fg-muted">None.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-left">
            <thead className="text-[12px] text-fg-muted">
              <tr className="border-b border-border">
                <th className="px-4 py-2 font-medium">Name</th>
                <th className="px-4 py-2 font-medium">Type</th>
                <th className="px-4 py-2 font-medium">SQL</th>
              </tr>
            </thead>
            <tbody>
              {members.map((m) => (
                <tr key={m.name} className="border-b border-border align-top last:border-0">
                  <td className="px-4 py-2">
                    <div className="flex items-center gap-1.5 font-mono text-[12.5px]">
                      {m.name}
                      {m.restrictedTo && (
                        <span title={`Only ${m.restrictedTo.join(", ")}`} className="inline-flex items-center gap-1 font-sans text-[12px] text-fg-muted">
                          <Lock className="size-3" aria-hidden /> {m.restrictedTo.join(", ")} only
                        </span>
                      )}
                    </div>
                    {m.description && <div className="text-[12px] text-fg-muted">{m.description}</div>}
                  </td>
                  <td className="px-4 py-2 text-fg-muted">{m.type.replace("_", " ")}</td>
                  <td className="px-4 py-2 font-mono text-[12px] text-fg-muted">{m.sql}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

export default async function CubePage({ params }: Props) {
  const blocked = await notConnected("data");
  if (blocked) return null;
  const cube = await getCube((await params).name);
  if (!cube) notFound();
  return (
    <div className="flex flex-col gap-4">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-mono text-[16px] font-semibold">{cube.name}</h2>
          <span className="text-fg-muted">{cube.title}</span>
        </div>
        <p className="mt-0.5 max-w-[72ch] text-fg-muted">{cube.description}</p>
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <Members title="Dimensions" members={cube.dimensions} />
          <Members title="Measures" members={cube.measures} />
        </div>
        <Panel className="self-start">
          <PanelHeader title="Details" />
          <div className="p-4">
            <DescList
              items={[
                { label: "Table", value: <span className="font-mono text-[12px]">{cube.table}</span> },
                { label: "Schema", value: <span className="font-mono text-[12px]">{cube.schema}</span> },
                { label: "Partitions", value: cube.partition ?? "None" },
                {
                  label: "Joins",
                  value: cube.joins.length ? (
                    <ul className="flex flex-col gap-1">
                      {cube.joins.map((j) => (
                        <li key={j.to}>
                          <Link href={`/data/cubes/${j.to}`} className="font-mono text-[12px] text-accent hover:underline">{j.to}</Link>{" "}
                          <span className="text-[12px] text-fg-muted">{j.relationship.replaceAll("_", " ")}</span>
                        </li>
                      ))}
                    </ul>
                  ) : "None",
                },
                { label: "Segments", value: cube.segments.length ? cube.segments.map((s) => <div key={s.name} className="font-mono text-[12px]">{s.name}</div>) : "None" },
                { label: "Row rules", value: cube.policies.length ? cube.policies.map((p) => <div key={p}>{p}</div>) : "None" },
                {
                  label: "Used by",
                  value: (
                    <div className="flex flex-wrap gap-1">
                      {cube.usedBy.map((a) => <Pill key={a}>{a}</Pill>)}
                    </div>
                  ),
                },
              ]}
            />
          </div>
        </Panel>
      </div>
    </div>
  );
}
