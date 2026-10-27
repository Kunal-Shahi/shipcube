import type { Metadata } from "next";
import { ChevronRight } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { CodeBlock } from "@/components/copy-button";
import { KindPill, MigrationStatusPill, describeOp } from "@/components/migration-meta";
import { Panel, PanelHeader } from "@/components/ui";
import { getMigration, getWarehouse } from "@/lib/api";
import { ago, time } from "@/lib/format";
import { ApplyPanel } from "./apply-panel";

type Props = { params: Promise<{ id: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  return { title: (await params).id };
}

const DIFF = [
  { key: "added", sign: "+", cls: "text-good" },
  { key: "changed", sign: "~", cls: "text-warning" },
  { key: "removed", sign: "−", cls: "text-critical" },
] as const;

export default async function MigrationPage({ params }: Props) {
  const [m, wh] = await Promise.all([getMigration((await params).id), getWarehouse()]);
  if (!m) notFound();
  const i = m.impact;
  const touched = i.breaks.length + i.changes.length + i.warnings.length;

  return (
    <>
      <div className="mb-3 flex items-center gap-1 text-[12px] text-fg-muted">
        <Link href="/data/migrations" className="hover:text-fg">Migrations</Link>
        <ChevronRight className="size-3" aria-hidden />
        <span className="font-mono text-fg">{m.id}</span>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
        <article className="flex min-w-0 flex-col gap-6">
          <header className="flex flex-col gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <KindPill kind={m.kind} />
              <MigrationStatusPill status={m.status} />
            </div>
            <h2 className="text-[20px] font-semibold leading-7 tracking-[-0.01em]">{m.description}</h2>
            <p className="text-fg-muted">
              Proposed by {m.proposedBy.agent} {ago(m.proposedAt)} for {m.proposedBy.user}
              {m.draftedBy && <>; drafted by {m.draftedBy}</>}. Based on model version {m.baseVersion}.
            </p>
          </header>

          {m.ops.length > 0 && (
            <section>
              <h3 className="text-[14px] font-semibold">Changes</h3>
              <ol className="mt-2 divide-y divide-border rounded-lg border border-border bg-surface">
                {m.ops.map((o, n) => {
                  const d = describeOp(o);
                  return (
                    <li key={n} className="flex flex-wrap items-baseline gap-x-2 px-4 py-2.5">
                      <span className="num w-5 text-[12px] text-fg-subtle">{n + 1}</span>
                      <span>{d.verb}</span>
                      <code className="font-mono text-[12.5px] font-medium">{d.target}</code>
                      {d.detail && <span className="w-full pl-7 font-mono text-[12px] text-fg-muted">{d.detail}</span>}
                    </li>
                  );
                })}
              </ol>
            </section>
          )}

          <section>
            <h3 className="text-[14px] font-semibold">Impact on saved queries</h3>
            <p className="mt-1 text-fg-muted">
              ShipCube compiled every saved query against the current model and this one.{" "}
              <span className="num">{touched}</span> {touched === 1 ? "differs" : "differ"}; <span className="num">{i.unaffected}</span> are unaffected.
            </p>
            {touched > 0 && (
              <div className="mt-2 overflow-x-auto rounded-lg border border-border bg-surface">
                <table className="w-full min-w-[560px] text-left">
                  <thead className="text-[12px] text-fg-muted">
                    <tr className="border-b border-border">
                      <th className="px-4 py-2 font-medium">Effect</th>
                      <th className="px-4 py-2 font-medium">Query</th>
                      <th className="px-4 py-2 font-medium">App</th>
                      <th className="px-4 py-2 font-medium">Detail</th>
                    </tr>
                  </thead>
                  <tbody>
                    {i.breaks.map((b) => (
                      <tr key={b.query} className="border-b border-border align-top last:border-0">
                        <td className="px-4 py-2 font-medium text-critical">Breaks</td>
                        <td className="px-4 py-2 font-mono text-[12.5px]">{b.query}</td>
                        <td className="px-4 py-2">{b.app}</td>
                        <td className="px-4 py-2 font-mono text-[12px] text-fg-muted">{b.error}</td>
                      </tr>
                    ))}
                    {i.changes.map((c) => (
                      <tr key={c.query} className="border-b border-border align-top last:border-0">
                        <td className="px-4 py-2 font-medium text-warning">Numbers change</td>
                        <td className="px-4 py-2 font-mono text-[12.5px]">{c.query}</td>
                        <td className="px-4 py-2">{c.app}</td>
                        <td className="px-4 py-2 text-fg-muted">{c.note}</td>
                      </tr>
                    ))}
                    {i.warnings.map((w) => (
                      <tr key={w.query} className="border-b border-border align-top last:border-0">
                        <td className="px-4 py-2 font-medium">Warning</td>
                        <td className="px-4 py-2 font-mono text-[12.5px]">{w.query}</td>
                        <td className="px-4 py-2">{w.app}</td>
                        <td className="px-4 py-2 text-fg-muted">{w.warnings.join(" ")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {(m.diff.added.length + m.diff.changed.length + m.diff.removed.length > 0) && (
            <section>
              <h3 className="text-[14px] font-semibold">Catalog diff</h3>
              <pre className="mt-2 overflow-x-auto rounded-lg border border-border bg-surface px-4 py-3 font-mono text-[12.5px] leading-6">
                {DIFF.flatMap(({ key, sign, cls }) =>
                  m.diff[key].map((row) => (
                    <div key={key + row} className={cls}>
                      {sign} {row}
                    </div>
                  )),
                )}
              </pre>
            </section>
          )}

          {m.ops.length > 0 && (
            <details className="group">
              <summary className="cursor-pointer text-[14px] font-semibold">Migration JSON</summary>
              <div className="mt-2">
                <CodeBlock label={`${m.id}.json`} code={JSON.stringify({ id: m.id, description: m.description, ops: m.ops }, null, 2)} />
              </div>
            </details>
          )}
        </article>

        <aside className="lg:sticky lg:top-8 lg:self-start">
          <Panel>
            <PanelHeader title={m.status === "proposed" ? "Review" : "Status"} />
            {m.status === "proposed" ? (
              <ApplyPanel m={m} nextVersion={wh.modelVersion + 1} />
            ) : m.applied ? (
              <p className="p-4 text-fg-muted">
                Applied as version {m.applied.version} by {m.applied.by} at <span className="num">{time(m.applied.at)}</span> UTC.
              </p>
            ) : (
              <p className="p-4 text-fg-muted">Rejected{m.rejected ? ` by ${m.rejected.by}` : ""}.</p>
            )}
          </Panel>
        </aside>
      </div>
    </>
  );
}
