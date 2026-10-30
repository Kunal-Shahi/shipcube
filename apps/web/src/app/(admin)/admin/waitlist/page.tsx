import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { EmptyState, PageHeader, Panel, TableSkeleton } from "@/components/ui";
import { listWaitlist } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { WaitlistState } from "@/lib/types";
import { WaitlistTable } from "./waitlist-table";

export const metadata: Metadata = { title: "Waitlist" };

const FILTERS: { id: WaitlistState | "all"; label: string }[] = [
  { id: "pending", label: "Pending" },
  { id: "approved", label: "Approved" },
  { id: "rejected", label: "Rejected" },
  { id: "all", label: "All" },
];

type Props = { searchParams: Promise<{ status?: string }> };

// Platform admins only: (admin)/layout.tsx answers 404 for everyone else, and the API refuses the
// list and the decisions for them anyway.
export default async function WaitlistAdminPage({ searchParams }: Props) {
  const { status } = await searchParams;
  const filter = FILTERS.find((f) => f.id === status)?.id ?? "pending";

  return (
    <>
      <PageHeader
        title="Waitlist"
        description="Early-access requests from the landing page. Approving one creates the person's workspace; they get it the first time they sign in with that email's Google account."
      />
      <nav aria-label="Filter by status" className="mb-3 flex gap-1">
        {FILTERS.map((f) => (
          <Link
            key={f.id}
            href={f.id === "pending" ? "/admin/waitlist" : `/admin/waitlist?status=${f.id}`}
            aria-current={f.id === filter ? "page" : undefined}
            className={cn(
              "rounded-md px-2.5 py-1 text-[13px]",
              f.id === filter ? "bg-surface-2 font-medium text-fg" : "text-fg-muted hover:bg-surface-2 hover:text-fg",
            )}
          >
            {f.label}
          </Link>
        ))}
      </nav>
      <Suspense key={filter} fallback={<Panel><TableSkeleton rows={5} cols={4} /></Panel>}>
        <Requests filter={filter} />
      </Suspense>
    </>
  );
}

async function Requests({ filter }: { filter: WaitlistState | "all" }) {
  const { requests, workspaces } = await listWaitlist(filter);
  const usage = workspaces && (
    <p className="mb-3 text-[12px] text-fg-muted">
      <span className="num">{workspaces.count}</span> of <span className="num">{workspaces.cap}</span> workspaces in use. Approving
      stops at the cap.
    </p>
  );
  if (!requests.length) {
    return (
      <>
        {usage}
        <Panel>
          <EmptyState
            title={filter === "pending" ? "No requests waiting" : "No requests here"}
            body="Requests from the landing page's form appear here, newest first."
          />
        </Panel>
      </>
    );
  }
  return (
    <>
      {usage}
      <Panel>
        <WaitlistTable initial={requests} />
      </Panel>
    </>
  );
}
