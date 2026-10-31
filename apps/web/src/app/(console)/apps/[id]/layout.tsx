import type { Metadata } from "next";
import { ChevronRight, ExternalLink } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { RouteTabs } from "@/components/tabs";
import { RestoreButton } from "@/components/restore-button";
import { HealthPill, Pill, TierBadge, buttonClass } from "@/components/ui";
import { ago, date, restoreWindow } from "@/lib/format";
import { getApp } from "@/lib/api";

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }): Promise<Metadata> {
  const app = await getApp((await params).id);
  return { title: app?.name ?? "App not found" };
}

export default async function AppLayout({ children, params }: { children: React.ReactNode; params: Promise<{ id: string }> }) {
  const { id } = await params;
  const app = await getApp(id);
  if (!app) notFound();
  const base = `/apps/${app.id}`;
  const isJob = app.kind === "job";
  const restore = app.deletedAt ? restoreWindow(app.deletedAt) : null;

  return (
    <>
      <div className="mb-3 flex items-center gap-1 text-[12px] text-fg-muted">
        <Link href="/apps" className="hover:text-fg">Apps</Link>
        <ChevronRight className="size-3" aria-hidden />
        <span className="text-fg">{app.name}</span>
      </div>
      <header className="mb-5 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="font-mono text-[18px] font-semibold leading-7 tracking-[-0.01em]">{app.name}</h1>
            {restore ? <Pill tone="critical">Deleted</Pill> : <HealthPill health={app.health} />}
            <TierBadge tier={app.tier} long />
          </div>
          <p className="mt-1 max-w-[72ch] text-fg-muted">{app.description}</p>
        </div>
        {!isJob && !restore && (
          <a href={app.url} target="_blank" rel="noreferrer" className={buttonClass("secondary")}>
            Open app <ExternalLink className="size-3.5" aria-hidden />
          </a>
        )}
      </header>
      {app.accessNote && !restore && !isJob && (
        <div role="status" className="mb-5 rounded-lg border border-warning/40 bg-warning-soft px-4 py-3">
          {app.accessNote}
        </div>
      )}
      {restore && app.deletedAt && (
        <div className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-critical/40 bg-critical-soft px-4 py-3">
          <p>
            <span className="font-medium">Deleted {ago(app.deletedAt)}.</span>{" "}
            <span className="text-fg-muted">
              The URL is off and nobody can open it. You can restore it until {date(restore.until)} ({restore.daysLeft}{" "}
              {restore.daysLeft === 1 ? "day" : "days"} left); after that ShipCube removes the service and its image.
            </span>
          </p>
          <RestoreButton appId={app.id} name={app.name} size="md" />
        </div>
      )}
      <RouteTabs
        tabs={[
          { href: base, label: "Overview" },
          { href: `${base}/access`, label: "Access", count: app.access.length },
          { href: `${base}/monitoring`, label: "Monitoring" },
          { href: `${base}/logs`, label: "Logs" },
        ]}
      />
      <div className="pt-6">{children}</div>
    </>
  );
}
