import { notFound } from "next/navigation";
import { DescList, Panel, PanelHeader, TierBadge } from "@/components/ui";
import { ReplacementSuggestion } from "@/components/replacement";
import { getApp } from "@/lib/api";
import { date, money, pct } from "@/lib/format";
import { TIERS } from "@/lib/types";
import { CapsForm } from "./caps-form";

const CLASS_LABEL: Record<string, string> = {
  batch: "Batch / cron",
  "bursty-interactive": "Bursty interactive",
  "steady-service": "Steady service",
  "memory-heavy": "Memory-heavy",
  "io-bound": "I/O-bound",
  "compute-heavy": "Compute-heavy",
};

export default async function AppOverview({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const app = await getApp(id);
  if (!app) notFound();
  const e = app.estimate;

  return (
    <div className="flex flex-col gap-4">
      {app.monitoring.replacement && <ReplacementSuggestion r={app.monitoring.replacement} />}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Panel>
          <PanelHeader
            title="Load estimate"
            description={`${e.estimatedBy} · ${date(e.estimatedAt)}`}
            actions={<span className="text-[12px] text-fg-muted">{e.overridden ? "Overridden by you" : "Recommended tier in use"}</span>}
          />
          <div className="grid grid-cols-2 border-b border-border sm:grid-cols-4 [&>div]:px-4 [&>div]:py-3 [&>div:not(:first-child)]:border-l [&>div]:border-border max-sm:[&>div:nth-child(3)]:border-l-0 max-sm:[&>div:nth-child(n+3)]:border-t">
            <div>
              <div className="text-[12px] font-medium text-fg-muted">Workload</div>
              <div className="mt-1 font-medium">{CLASS_LABEL[e.workloadClass]}</div>
              <div className="num text-[12px] text-fg-muted">{pct(e.confidence * 100, 0)} confidence</div>
            </div>
            <div>
              <div className="text-[12px] font-medium text-fg-muted">Tier</div>
              <div className="mt-1"><TierBadge tier={e.recommendedTier} long /></div>
              <div className="text-[12px] text-fg-muted">{TIERS[e.recommendedTier].target}</div>
            </div>
            <div>
              <div className="text-[12px] font-medium text-fg-muted">Resources</div>
              <div className="mt-1 font-medium">{e.cpu} · {e.memory}</div>
              <div className="num text-[12px] text-fg-muted">{e.minInstances}–{e.maxInstances} instances</div>
            </div>
            <div>
              <div className="text-[12px] font-medium text-fg-muted">Estimated cost</div>
              <div className="num mt-1 text-[18px] font-semibold leading-6">{money(e.monthlyCost)}</div>
              <div className="text-[12px] text-fg-muted">per month</div>
            </div>
          </div>
          <div className="p-4">
            <h3 className="text-[12px] font-medium text-fg-muted">Why this tier</h3>
            <ol className="mt-2 flex max-w-[78ch] list-decimal flex-col gap-1.5 pl-5 marker:text-fg-subtle">
              {e.reasoning.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ol>
            <h3 className="mt-5 text-[12px] font-medium text-fg-muted">Measured in the sandbox run</h3>
            <dl className="mt-2 grid grid-cols-2 gap-px overflow-hidden rounded-md border border-border bg-border sm:grid-cols-3">
              {e.signals.map((s) => (
                <div key={s.label} className="bg-surface px-3 py-2">
                  <dt className="text-[12px] text-fg-muted">{s.label}</dt>
                  <dd className="num font-mono text-[12.5px]">{s.value}</dd>
                </div>
              ))}
            </dl>
          </div>
        </Panel>

        <Panel>
          <PanelHeader title="Details" />
          <div className="p-4">
            <DescList
              items={[
                { label: "URL", value: app.kind === "job" ? <span className="text-fg-muted">Scheduled job, no URL</span> : <a href={app.url} className="font-mono text-[12px] text-accent hover:underline break-all" target="_blank" rel="noreferrer">{app.url.replace("https://", "")}</a> },
                { label: "Runs on", value: app.billing === "own-gcp" ? "Your GCP project" : "ShipCube's GCP project" },
                { label: "Region", value: <span className="font-mono text-[12px]">{app.region}</span> },
                { label: "AI provider", value: app.aiProvider },
                { label: "Source", value: <span className="font-mono text-[12px] break-all">{app.repo}</span> },
                { label: "Deployed by", value: app.deployedBy },
                { label: "Created", value: date(app.createdAt) },
                { label: "App ID", value: <span className="font-mono text-[12px]">{app.id}</span> },
              ]}
            />
          </div>
        </Panel>
      </div>

      <Panel>
        <PanelHeader
          title="Caps"
          description="Hard limits for this app. ShipCube enforces them at the gateway and in Cloud Run, so a runaway loop can't run up a bill."
        />
        <CapsForm caps={app.caps} usage={app.usage} appName={app.name} />
      </Panel>
    </div>
  );
}
