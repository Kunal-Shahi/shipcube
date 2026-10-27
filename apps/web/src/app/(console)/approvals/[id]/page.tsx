import type { Metadata } from "next";
import { ArrowRight, ChevronRight } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { APPROVAL_KIND, ApprovalStatusPill } from "@/components/approval-meta";
import { CodeBlock } from "@/components/copy-button";
import { Panel, PanelHeader } from "@/components/ui";
import { getApproval } from "@/lib/api";
import { cn } from "@/lib/cn";
import { ago, time } from "@/lib/format";
import type { ImpactRow } from "@/lib/types";
import { DecisionPanel } from "./decision-panel";

type Props = { params: Promise<{ id: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const a = await getApproval((await params).id);
  return { title: a ? `Approve: ${a.appName}` : "Approval not found" };
}

const TONE: Record<NonNullable<ImpactRow["tone"]>, string> = { warning: "text-warning", critical: "text-critical" };

export default async function ApprovalPage({ params }: Props) {
  const a = await getApproval((await params).id);
  if (!a) notFound();
  const k = APPROVAL_KIND[a.kind];
  const Icon = k.icon;
  const hoursLeft = Math.max(0, Math.round((new Date(a.expiresAt).getTime() - Date.now()) / 3600_000));
  const hasBefore = a.impact.some((r) => r.before);
  const agent = a.requestedBy?.agent ?? "An agent";

  return (
    <>
      <div className="mb-3 flex items-center gap-1 text-[12px] text-fg-muted">
        <Link href="/approvals" className="hover:text-fg">Approvals</Link>
        <ChevronRight className="size-3" aria-hidden />
        <span className="font-mono text-fg">{a.id}</span>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
        <article className="flex min-w-0 flex-col gap-6">
          <header className="flex flex-col gap-2">
            <div className="flex flex-wrap items-center gap-2 text-[12px] text-fg-muted">
              <Icon className="size-4" aria-hidden />
              {k.label}
              <ApprovalStatusPill status={a.status} />
            </div>
            <h1 className="text-[22px] font-semibold leading-8 tracking-[-0.015em]">{a.title}</h1>
            <p className="text-fg-muted">
              {agent} asked {ago(a.requestedAt)}
              {a.requestedBy && <>, using your token &ldquo;{a.requestedBy.tokenName}&rdquo;</>}.
              {a.appId && (
                <>
                  {" "}
                  <Link href={`/apps/${a.appId}`} className="text-accent hover:underline">Open {a.appName}</Link>
                </>
              )}
            </p>
          </header>

          {a.prompt && (
            <figure className="rounded-lg border border-border bg-surface px-4 py-3">
              <figcaption className="text-[12px] text-fg-muted">What you asked {agent}</figcaption>
              <blockquote className="mt-1 text-[15px] leading-6">&ldquo;{a.prompt}&rdquo;</blockquote>
            </figure>
          )}

          {a.impact.length > 0 && (
          <section>
            <h2 className="text-[14px] font-semibold">What changes</h2>
            <div className="mt-2 overflow-x-auto rounded-lg border border-border bg-surface">
              <table className="w-full min-w-[480px] text-left">
                {hasBefore && (
                  <thead className="text-[12px] text-fg-muted">
                    <tr className="border-b border-border">
                      <th className="w-[30%] px-4 py-2 font-medium" />
                      <th className="px-4 py-2 font-medium">Now</th>
                      <th className="w-6" />
                      <th className="px-4 py-2 font-medium">If approved</th>
                    </tr>
                  </thead>
                )}
                <tbody>
                  {a.impact.map((r) => (
                    <tr key={r.label} className="border-b border-border align-top last:border-0">
                      <th scope="row" className="w-[30%] px-4 py-2.5 font-normal text-fg-muted">{r.label}</th>
                      {hasBefore && r.before ? (
                        <>
                          <td className="num px-4 py-2.5 text-fg-muted">{r.before}</td>
                          <td className="py-2.5 text-fg-subtle"><ArrowRight className="mt-0.5 size-3.5" aria-label="becomes" /></td>
                          <td className={cn("num px-4 py-2.5 font-medium", r.tone && TONE[r.tone])}>{r.after}</td>
                        </>
                      ) : (
                        <td colSpan={hasBefore ? 3 : 1} className={cn("num px-4 py-2.5", r.tone && TONE[r.tone])}>{r.after}</td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          )}

          <section>
            <h2 className="text-[14px] font-semibold">Why it needs you</h2>
            <p className="mt-1 max-w-[72ch]">{a.reason}</p>
          </section>

          <section>
            <h2 className="text-[14px] font-semibold">{a.status === "pending" ? "What happens if you approve" : "What it does"}</h2>
            <ol className="mt-2 flex max-w-[72ch] list-decimal flex-col gap-1 pl-5 marker:text-fg-subtle">
              {a.effects.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ol>
          </section>

          {a.museNote && (
            <section className="rounded-lg border border-border bg-surface px-4 py-3">
              <h2 className="text-[12px] font-medium text-fg-muted">Muse Spark&apos;s assessment</h2>
              <p className="mt-1 max-w-[72ch]">{a.museNote}</p>
            </section>
          )}

          {a.tool && Object.keys(a.tool.args).length > 0 && (
          <section>
            <h2 className="text-[14px] font-semibold">Exact request</h2>
            <p className="mt-1 text-fg-muted">The tool call ShipCube received. Approving runs exactly this, nothing else.</p>
            <div className="mt-2">
              <CodeBlock label={a.tool.name} code={JSON.stringify(a.tool.args, null, 2)} />
            </div>
          </section>
          )}
        </article>

        <aside className="lg:sticky lg:top-8 lg:self-start">
          <Panel>
            <PanelHeader title="Decision" />
            {a.status === "pending" ? (
              <DecisionPanel approval={a} hoursLeft={hoursLeft} />
            ) : (
              <div className="flex flex-col gap-2 p-4">
                <ApprovalStatusPill status={a.status} />
                {a.decision ? (
                  <p className="text-fg-muted">
                    {a.decision.by && a.decision.by !== "you" && a.decision.by !== a.requestedBy?.user ? a.decision.by : "You"} {a.status} this at <span className="num">{time(a.decision.at)}</span> UTC.
                  </p>
                ) : (
                  <p className="text-fg-muted">Nobody decided within 72 hours, so the agent was told the request expired.</p>
                )}
                {a.decision?.note && <blockquote className="rounded-md bg-surface-2 px-3 py-2">{a.decision.note}</blockquote>}
              </div>
            )}
          </Panel>
        </aside>
      </div>
    </>
  );
}
