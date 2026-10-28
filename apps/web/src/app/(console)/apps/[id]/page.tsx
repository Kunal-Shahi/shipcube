import { notFound } from "next/navigation";
import { CodeBlock } from "@/components/copy-button";
import { DescList, EmptyState, Panel, PanelHeader, TierBadge } from "@/components/ui";
import { ReplacementSuggestion } from "@/components/replacement";
import { getApp } from "@/lib/api";
import { cn } from "@/lib/cn";
import { date, money, pct } from "@/lib/format";
import { TIERS, type LoadEstimate } from "@/lib/types";
import { CapsForm } from "./caps-form";

const CLASS_LABEL: Record<string, string> = {
  batch: "Batch / cron",
  "bursty-interactive": "Bursty interactive",
  "steady-service": "Steady service",
  "memory-heavy": "Memory-heavy",
  "io-bound": "I/O-bound",
  "compute-heavy": "Compute-heavy",
};

/** A known class's label, else the API's own words; never blank. */
const classLabel = (c: string) => CLASS_LABEL[c] ?? (c ? c.replaceAll("-", " ").replace(/^./, (x) => x.toUpperCase()) : "—");

const explainer = (by: string) => (by === "rules" ? "Rule-based explanation (Muse Spark was unavailable)" : `Explained by ${by}`);

function EstimatePanel({ e, appName }: { e?: LoadEstimate; appName: string }) {
  if (!e) {
    return (
      <Panel>
        <PanelHeader title="Load estimate" />
        <EmptyState
          title="No load estimate on record"
          body={
            <>
              <p>ShipCube keeps the estimate from the deploy that created this app. This app has none, so its tier was chosen without one. Ask your agent to run one:</p>
              <div className="mt-3 max-w-[560px]">
                <CodeBlock label="Prompt" wrap code={`Estimate the load for ${appName} with ShipCube and tell me if its tier still fits`} />
              </div>
            </>
          }
        />
      </Panel>
    );
  }
  return (
    <Panel>
      <PanelHeader
        title="Load estimate"
        description={`Numbers from the ${e.estimatedBy} on ${date(e.estimatedAt)}`}
        actions={<span className="text-[12px] text-fg-muted">{e.overridden ? "Overridden by you" : "Recommended tier in use"}</span>}
      />
      <div className="grid grid-cols-2 border-b border-border sm:grid-cols-4 [&>div]:px-4 [&>div]:py-3 [&>div:not(:first-child)]:border-l [&>div]:border-border max-sm:[&>div:nth-child(3)]:border-l-0 max-sm:[&>div:nth-child(n+3)]:border-t">
        <div>
          <div className="text-[12px] font-medium text-fg-muted">Workload</div>
          <div className="mt-1 font-medium">{classLabel(e.workloadClass)}</div>
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
          <div className="num mt-1 text-[18px] font-semibold leading-6">{e.monthlyCost === undefined ? "—" : money(e.monthlyCost)}</div>
          <div className="num text-[12px] text-fg-muted">
            per month{e.monthlyCostRange && `, ${money(e.monthlyCostRange.low)}–${money(e.monthlyCostRange.high)}`}
          </div>
        </div>
      </div>
      <div className="p-4">
        {e.reasoning.length > 0 ? (
          <>
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h3 className="text-[12px] font-medium text-fg-muted">Why this tier</h3>
              <span className="text-[12px] text-fg-muted">{explainer(e.explainedBy)}</span>
            </div>
            <ol className="mt-2 flex max-w-[78ch] list-decimal flex-col gap-1.5 pl-5 marker:text-fg-subtle">
              {e.reasoning.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ol>
          </>
        ) : (
          // The API's estimate carries the decision, not yet the reasoning or the sandbox signals.
          <p className="text-[12px] text-fg-muted">{explainer(e.explainedBy)}</p>
        )}
        {e.signals.length > 0 && (
          <>
            <h3 className="mt-5 text-[12px] font-medium text-fg-muted">Measured in the sandbox run</h3>
            <dl className={cn("mt-2 grid grid-cols-2 gap-px overflow-hidden rounded-md border border-border bg-border", e.signals.length % 3 === 0 && "sm:grid-cols-3")}>
              {e.signals.map((s) => (
                <div key={s.label} className="bg-surface px-3 py-2">
                  <dt className="text-[12px] text-fg-muted">{s.label}</dt>
                  <dd className="num font-mono text-[12.5px]">{s.value}</dd>
                </div>
              ))}
            </dl>
          </>
        )}
      </div>
    </Panel>
  );
}

export default async function AppOverview({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const app = await getApp(id);
  if (!app) notFound();
  const replacement = app.monitoring?.replacement;

  const details: { label: string; value: React.ReactNode }[] = [
    {
      label: "URL",
      value:
        app.kind === "job" ? (
          <span className="text-fg-muted">Scheduled job, no URL</span>
        ) : (
          <a href={app.url} className="font-mono text-[12px] break-all text-accent hover:underline" target="_blank" rel="noreferrer">
            {app.url.replace("https://", "")}
          </a>
        ),
    },
    {
      label: "Runs on",
      value: app.runsLocally ? "Local (Docker)" : app.billing === undefined ? null : app.billing === "own-gcp" ? "Your GCP project" : "ShipCube's GCP project",
    },
    { label: "Region", value: app.region && <span className="font-mono text-[12px]">{app.region}</span> },
    { label: "Revision", value: app.revision && <span className="font-mono text-[12px] break-all">{app.revision}</span> },
    { label: "AI provider", value: app.aiProvider },
    { label: "Source", value: app.repo && <span className="font-mono text-[12px] break-all">{app.repo}</span> },
    { label: "Deployed by", value: app.deployedBy },
    { label: "Created", value: app.createdAt && date(app.createdAt) },
    { label: "Last deploy", value: date(app.lastDeployAt) },
    { label: "App ID", value: <span className="font-mono text-[12px]">{app.id}</span> },
  ].filter((d) => d.value);

  return (
    <div className="flex flex-col gap-4">
      {replacement && <ReplacementSuggestion r={replacement} />}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <EstimatePanel e={app.estimate} appName={app.name} />
        <Panel className="self-start">
          <PanelHeader title="Details" />
          <div className="p-4">
            <DescList items={details} />
            {!app.deletedAt && (
              // Deleting is agent-only by design: delete_app always waits for a person's approval.
              <p className="mt-4 border-t border-border pt-3 text-[12px] text-fg-muted">
                To delete this app, ask your agent (&ldquo;Delete {app.name}&rdquo;). The deletion waits for your approval under
                Approvals, and you can restore the app for 7 days after.
              </p>
            )}
          </div>
        </Panel>
      </div>

      <Panel>
        <PanelHeader
          title="Caps"
          description="Hard limits for this app. ShipCube enforces them at the gateway and in Cloud Run, so a runaway loop can't run up a bill."
        />
        <CapsForm appId={app.id} caps={app.caps} usage={app.usage ?? {}} appName={app.name} />
      </Panel>
    </div>
  );
}
