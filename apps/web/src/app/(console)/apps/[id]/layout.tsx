import type { Metadata } from "next";
import { ChevronRight, ExternalLink } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { RouteTabs } from "@/components/tabs";
import { HealthPill, TierBadge, buttonClass } from "@/components/ui";
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
            <HealthPill health={app.health} />
            <TierBadge tier={app.tier} long />
          </div>
          <p className="mt-1 max-w-[72ch] text-fg-muted">{app.description}</p>
        </div>
        {!isJob && (
          <a href={app.url} target="_blank" rel="noreferrer" className={buttonClass("secondary")}>
            Open app <ExternalLink className="size-3.5" aria-hidden />
          </a>
        )}
      </header>
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
