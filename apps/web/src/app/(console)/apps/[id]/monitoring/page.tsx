import { AlertTriangle, CheckCircle2 } from "lucide-react";
import { notFound } from "next/navigation";
import { CostChart, LatencyChart } from "@/components/charts";
import { ReplacementSuggestion } from "@/components/replacement";
import { EmptyState, FreshnessPill, HealthPill, Meter, Panel, PanelHeader, Pill, Stat, StatRow } from "@/components/ui";
import { demoState, getApp } from "@/lib/api";
import { ago, count, money, pct } from "@/lib/format";

type Props = {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

function sla(minutes: number) {
  return minutes >= 1440 ? `${minutes / 1440} day` : minutes >= 60 ? `${minutes / 60} h` : `${minutes} min`;
}

export default async function MonitoringPage({ params, searchParams }: Props) {
  const [{ id }, sp] = await Promise.all([params, searchParams]);
  const app = await getApp(id, demoState(sp));
  if (!app) notFound();
  const m = app.monitoring;

  if (m.latency.length === 0 || demoState(sp) === "empty") {
    return (
      <Panel>
        <EmptyState
          title="No metrics yet"
          body={`ShipCube starts collecting latency, freshness, cost and drift after ${app.name} serves its first request. Deploys usually finish within two minutes; the Logs tab shows progress.`}
        />
      </Panel>
    );
  }

  const driftWarn = m.driftScore >= 0.1;
  const p95Warn = m.p95Ms > 1000;

  return (
    <div className="flex flex-col gap-4">
      {m.replacement && <ReplacementSuggestion r={m.replacement} />}

      <StatRow>
        <Stat
          label="Health"
          status={<HealthPill health={m.health} />}
          value={pct(m.uptime30d, 2)}
          note={<span className="num">uptime, 30 days · {pct(m.errorRate)} errors, 24 h</span>}
        />
        <Stat
          label="p95 latency"
          status={p95Warn ? <Pill tone="warning" icon={<AlertTriangle className="size-3.5" aria-hidden />}>Slow</Pill> : undefined}
          value={`${count(m.p95Ms)} ms`}
          note={<span className="num">p50 {m.p50Ms} ms · {m.coldStarts24h} cold starts, 24 h</span>}
        />
        <Stat
          label="Data freshness"
          status={<FreshnessPill freshness={m.freshness} />}
          value={ago(m.sourceUpdatedAt)}
          note={`source updated · target under ${sla(m.freshnessSlaMinutes)}`}
        />
        <Stat
          label="Cost this month"
          value={money(m.costMonthToDate)}
          note={
            <span className="flex flex-col gap-1.5 pt-0.5">
              <Meter value={m.costMonthToDate} max={app.caps.monthlySpend} label="Spend against cap" />
              <span className="num">of {money(app.caps.monthlySpend)} cap</span>
            </span>
          }
        />
      </StatRow>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel>
          <PanelHeader title="Latency" description="p50 and p95, last 24 hours, UTC" />
          <div className="p-4">
            <LatencyChart data={m.latency} />
          </div>
        </Panel>
        <Panel>
          <PanelHeader title="Daily cost" description="October 2026, compute + queries + LLM tokens" />
          <div className="p-4">
            <CostChart data={m.dailyCost} />
          </div>
        </Panel>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel>
          <PanelHeader title="Output drift" description="Compares each run's output distribution with the previous 7 runs" />
          <div className="flex items-start gap-3 p-4">
            {driftWarn ? (
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
            ) : (
              <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-good" aria-hidden />
            )}
            <div>
              <div className="num font-medium">
                Drift score {m.driftScore.toFixed(2)}{" "}
                <span className="font-normal text-fg-muted">(alert at 0.10)</span>
              </div>
              <p className="mt-0.5 text-fg-muted">{m.driftNote}</p>
            </div>
          </div>
        </Panel>
        <Panel>
          <PanelHeader title="Cost breakdown" description="This month" />
          <table className="w-full">
            <tbody>
              {m.costBreakdown.map((c) => (
                <tr key={c.label} className="border-b border-border last:border-0">
                  <td className="px-4 py-2">{c.label}</td>
                  <td className="w-1/2 px-4 py-2">
                    <div className="h-1.5 rounded-full bg-surface-2">
                      <div className="h-full rounded-full bg-accent" style={{ width: `${(c.value / m.costMonthToDate) * 100}%` }} />
                    </div>
                  </td>
                  <td className="num px-4 py-2 text-right">{money(c.value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      </div>
    </div>
  );
}
