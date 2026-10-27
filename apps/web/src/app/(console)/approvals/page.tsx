import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { notConnected } from "@/components/not-connected";
import { APPROVAL_KIND, ApprovalStatusPill } from "@/components/approval-meta";
import { EmptyState, PageHeader, Panel, PanelHeader, TableSkeleton } from "@/components/ui";
import { demoState, listApprovals, listMigrations, supports, type DemoState } from "@/lib/api";
import { ago, date } from "@/lib/format";
import type { Approval } from "@/lib/types";

export const metadata: Metadata = { title: "Approvals" };

type Props = { searchParams: Promise<Record<string, string | string[] | undefined>> };

export default async function ApprovalsPage({ searchParams }: Props) {
  const state = demoState(await searchParams);
  return (
    <>
      <PageHeader
        title="Approvals"
        description="Agents act on their own for most things. These few actions wait for a person: the first deploy of an app into your GCP project, raising a cap above the workspace default, sharing with a whole domain, and deleting an app."
      />
      <Suspense key={state} fallback={<Panel><TableSkeleton rows={4} cols={4} /></Panel>}>
        <ApprovalLists state={state} />
      </Suspense>
    </>
  );
}

function Row({ a }: { a: Approval }) {
  const k = APPROVAL_KIND[a.kind];
  const Icon = k.icon;
  return (
    <li className="group relative flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 hover:bg-surface-2">
      <span className="grid size-8 shrink-0 place-items-center rounded-md border border-border bg-surface text-fg-muted">
        <Icon className="size-4" aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <Link href={`/approvals/${a.id}`} className="font-medium after:absolute after:inset-0">
          {a.title}
        </Link>
        <div className="text-[12px] text-fg-muted">
          {k.label}. Requested {a.requestedBy ? `by ${a.requestedBy.agent} ` : ""}{ago(a.requestedAt)}
          {a.decision && `, ${a.status} on ${date(a.decision.at)}`}
        </div>
      </div>
      <ApprovalStatusPill status={a.status} />
    </li>
  );
}

async function ApprovalLists({ state }: { state: DemoState }) {
  const blocked = await notConnected("approvals_inbox");
  if (blocked) return blocked;
  const [all, migrations] = await Promise.all([listApprovals(state), supports("data").then((ok) => (ok ? listMigrations(state) : []))]);
  const pending = all.filter((a) => a.status === "pending");
  const decided = all.filter((a) => a.status !== "pending");
  const proposals = migrations.filter((m) => m.status === "proposed").length;

  return (
    <div className="flex flex-col gap-4">
      <Panel>
        <PanelHeader title="Waiting for you" description="Requests expire after 72 hours. The agent is told and can ask again." />
        {pending.length === 0 ? (
          <EmptyState
            title="Nothing to approve"
            body="When an agent asks for something that needs a person, it appears here and the agent shows you a link to it."
          />
        ) : (
          <ul className="divide-y divide-border">
            {pending.map((a) => (
              <Row key={a.id} a={a} />
            ))}
          </ul>
        )}
        {proposals > 0 && (
          <div className="border-t border-border px-4 py-2.5 text-fg-muted">
            {proposals} data model {proposals === 1 ? "change is" : "changes are"} also waiting for review in{" "}
            <Link href="/data/migrations" className="text-accent hover:underline">Data → Migrations</Link>.
          </div>
        )}
      </Panel>
      <Panel>
        <PanelHeader title="History" />
        {decided.length === 0 ? (
          <EmptyState title="No decisions yet" body="Approved, rejected and expired requests are kept here for 90 days." />
        ) : (
          <ul className="divide-y divide-border">
            {decided.map((a) => (
              <Row key={a.id} a={a} />
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}
