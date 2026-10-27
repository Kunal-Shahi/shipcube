import { AlertTriangle, CheckCircle2 } from "lucide-react";
import { notFound } from "next/navigation";
import { CostChart, LatencyChart } from "@/components/charts";
import { ReplacementSuggestion } from "@/components/replacement";
import { EmptyState, FreshnessPill, HealthPill, Meter, Panel, PanelHeader, Pill, Stat, StatRow } from "@/components/ui";
import { demoState, getApp } from "@/lib/api";
import { ago, count, money, monthLabel, pct } from "@/lib/format";

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

  if (!m || (m.requests === 0 && m.latency.length === 0 && m.p95Ms === 0)) {
    return (
      <Panel>
        <EmptyState
          title="No metrics yet"
          body={`ShipCube starts collecting latency, errors and cost after ${app.name} serves its first request. Deploys usually finish within two minutes; the Logs tab shows progress.`}
        />
      </Panel>
    );
  }

  const driftWarn = (m.driftScore ?? 0) >= 0.1;
  const p95Warn = m.p95Ms > 1000;
  const costTotal = m.costBreakdown.reduce((s, c) => s + c.value, 0);
  const costShown = m.costMonthToDate ?? costTotal;

  return (
    <div className="flex flex-col gap-4">
      {m.replacement && <ReplacementSuggestion r={m.replacement} />}

      <StatRow>
        <Stat
          label="Health"
          status={<HealthPill health={m.health} />}
          value={m.uptime30d !== undefined ? pct(m.uptime30d, 2) : pct(100 - m.errorRate, 2)}
          note={
            <span className="num">
              {m.uptime30d !== undefined ? "uptime, 30 days" : `successful requests, ${m.window}`}, {pct(m.errorRate)} errors
              {m.requests !== undefined && `, ${count(m.requests)} requests`}
            </span>
          }
        />
        <Stat
          label="p95 latency"
          status={p95Warn ? <Pill tone="warning" icon={<AlertTriangle className="size-3.5" aria-hidden />}>Slow</Pill> : undefined}
          value={`${count(m.p95Ms)} ms`}
          note={<span className="num">p50 {m.p50Ms} ms, {m.coldStarts24h} cold starts, {m.window}</span>}
        />
        <Stat
          label="Data freshness"
          status={<FreshnessPill freshness={m.freshness} />}
          value={m.sourceUpdatedAt ? ago(m.sourceUpdatedAt) : "Not tracked"}
          note={
            m.sourceUpdatedAt
              ? `source updated${m.freshnessSlaMinutes ? `, target under ${sla(m.freshnessSlaMinutes)}` : ""}`
              : "This app doesn't report a data source"
          }
        />
        <Stat
          label={m.costMonthToDate !== undefined ? "Cost this month" : `Cost, ${m.costWindow}`}
          value={money(costShown)}
          note={
            app.caps.monthlySpend > 0 && m.costMonthToDate !== undefined ? (
              <span className="flex flex-col gap-1.5 pt-0.5">
                <Meter value={m.costMonthToDate} max={app.caps.monthlySpend} label="Spend against cap" />
                <span className="num">of {money(app.caps.monthlySpend)} cap</span>
              </span>
            ) : (
              <span className="num">Monthly cap {money(app.caps.monthlySpend)}</span>
            )
          }
        />
      </StatRow>

      {(m.latency.length > 0 || m.dailyCost.length > 0) && (
        <div className="grid gap-4 lg:grid-cols-2">
          {m.latency.length > 0 && (
            <Panel>
              <PanelHeader title="Latency" description="p50 and p95, last 24 hours, UTC" />
              <div className="p-4">
                <LatencyChart data={m.latency} />
              </div>
            </Panel>
          )}
          {m.dailyCost.length > 0 && (
            <Panel>
              <PanelHeader title="Daily cost" description={`${monthLabel(m.dailyCost[0].t)}, compute, queries and LLM tokens`} />
              <div className="p-4">
                <CostChart data={m.dailyCost} />
              </div>
            </Panel>
          )}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        {m.driftScore !== undefined && (
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
                  Drift score {m.driftScore.toFixed(2)} <span className="font-normal text-fg-muted">(alert at 0.10)</span>
                </div>
                {m.driftNote && <p className="mt-0.5 text-fg-muted">{m.driftNote}</p>}
              </div>
            </div>
          </Panel>
        )}
        {m.costBreakdown.length > 0 && (
          <Panel>
            <PanelHeader title="Cost breakdown" description={m.costWindow.replace(/^./, (c) => c.toUpperCase())} />
            <table className="w-full">
              <caption className="sr-only">Cost by category, {m.costWindow}</caption>
              <tbody>
                {m.costBreakdown.map((c) => (
                  <tr key={c.label} className="border-b border-border last:border-0">
                    <th scope="row" className="px-4 py-2 text-left font-normal">{c.label}</th>
                    <td className="w-1/2 px-4 py-2">
                      <div className="h-1.5 rounded-full bg-surface-2" aria-hidden>
                        <div className="h-full rounded-full bg-accent" style={{ width: `${costTotal ? (c.value / costTotal) * 100 : 0}%` }} />
                      </div>
                    </td>
                    <td className="num px-4 py-2 text-right">{money(c.value)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>
        )}
      </div>
    </div>
  );
}
