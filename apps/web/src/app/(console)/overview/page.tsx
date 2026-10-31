import type { Metadata } from "next";
import { ArrowRight, Inbox, Rocket } from "lucide-react";
import Link from "next/link";
import { CostChart } from "@/components/charts";
import { Donut, Sparkline, StackedBars } from "@/components/dashboard-charts";
import { ButtonLink, EmptyState, HealthPill, Meter, PageHeader, Panel, PanelHeader, Stat, StatRow, TierBadge } from "@/components/ui";
import { demoState, getApp, getBilling, listApprovals, listApps, supports, type DemoState } from "@/lib/api";
import { ago, count, money, pct, short } from "@/lib/format";
import type { AppDetail, AppSummary, Approval, BillingOverview } from "@/lib/types";

export const metadata: Metadata = { title: "Overview" };

type Props = { searchParams: Promise<Record<string, string | string[] | undefined>> };

// Monitoring is fetched per app; the busiest few are enough for a first look.
const DETAIL_LIMIT = 8;

// The workspace at a glance: what's running and how it's doing, what it costs, and what's waiting
// for a person. Every number comes from the API (apps, their metrics, billing, approvals); a part
// the API can't serve is left out with a note, never filled in.
export default async function OverviewPage({ searchParams }: Props) {
  const state = demoState(await searchParams);
  const [apps, billing, approvals] = await Promise.all([
    listApps(state).catch(() => null),
    loadBilling(state),
    listApprovals(state).catch(() => null),
  ]);
  const live = (apps ?? []).filter((a) => !a.deletedAt);
  const busiest = [...live].sort((a, b) => (b.requests24h ?? 0) - (a.requests24h ?? 0)).slice(0, DETAIL_LIMIT);
  const details = (await Promise.all(busiest.map((a) => getApp(a.id, state).catch(() => null)))).filter(
    (d): d is AppDetail => d !== null,
  );
  const pending = (approvals ?? []).filter((a) => a.status === "pending");

  if (apps && live.length === 0) {
    return (
      <>
        <PageHeader title="Overview" />
        <Panel>
          <EmptyState
            title="Nothing deployed yet"
            body="Connect Claude Code, Cursor or another agent, then ask it to deploy something. Its health, cost and approvals show up here."
            action={<ButtonLink href="/setup" variant="primary">Connect your agent</ButtonLink>}
          />
        </Panel>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Overview"
        description="How your apps are doing, what they cost, and what's waiting for you."
        actions={<ButtonLink href="/apps">All apps</ButtonLink>}
      />
      <div className="flex flex-col gap-4">
        <Headline apps={live} billing={billing} pending={pending} />
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)] [&>*]:min-w-0">
          <SpendPanel billing={billing} />
          <BudgetPanel billing={billing} details={details} />
        </div>
        <HealthPanel apps={busiest} details={details} total={live.length} />
        <div className="grid gap-4 lg:grid-cols-2 [&>*]:min-w-0">
          <CostByAppPanel billing={billing} />
          <TrafficPanel apps={live} details={details} />
        </div>
        <div className="grid gap-4 lg:grid-cols-2 [&>*]:min-w-0">
          <WaitingPanel pending={pending} unavailable={approvals === null} />
          <DeploysPanel apps={live} />
        </div>
      </div>
    </>
  );
}

async function loadBilling(state: DemoState): Promise<BillingOverview | null> {
  if (!(await supports("billing"))) return null;
  return getBilling(state).catch(() => null);
}

function Headline({ apps, billing, pending }: { apps: AppSummary[]; billing: BillingOverview | null; pending: Approval[] }) {
  const healthy = apps.filter((a) => a.health === "healthy").length;
  const troubled = apps.filter((a) => a.health === "degraded" || a.health === "down").length;
  const requests = apps.reduce((s, a) => s + (a.requests24h ?? 0), 0);
  return (
    <StatRow>
      <Stat
        label="Apps healthy"
        value={`${healthy} of ${apps.length}`}
        note={troubled ? `${troubled} need${troubled === 1 ? "s" : ""} a look` : "Nothing degraded or down"}
      />
      <Stat
        label="Spend this month"
        value={billing ? money(billing.monthToDate) : "Not available"}
        note={billing ? `Forecast ${money(billing.forecast)} by month end` : "The API doesn't serve billing yet"}
      />
      <Stat label="Requests, last 24 hours" value={short(requests)} note={`Across ${apps.length} app${apps.length === 1 ? "" : "s"}`} />
      <Stat
        label="Waiting for you"
        value={count(pending.length)}
        note={
          pending.length ? (
            <Link href="/approvals" className="text-accent hover:underline">
              Review approvals
            </Link>
          ) : (
            "No approvals pending"
          )
        }
      />
    </StatRow>
  );
}

function SpendPanel({ billing }: { billing: BillingOverview | null }) {
  const days = billing?.daily ?? [];
  return (
    <Panel>
      <PanelHeader title="Daily spend" description="Every app in the workspace, compute, data and AI, last 30 days" />
      <div className="p-4">
        {days.some((d) => d.value > 0) ? (
          <>
            <CostChart data={days} />
            <SpendSummary billing={billing!} />
          </>
        ) : (
          <p className="py-10 text-center text-fg-muted">{billing ? "No spend recorded yet." : "Billing isn't available from this API yet."}</p>
        )}
      </div>
    </Panel>
  );
}

/** Four plain numbers under the chart: what's spent, where it's heading, a normal day, the busiest day. */
function SpendSummary({ billing }: { billing: BillingOverview }) {
  const spent = billing.daily.filter((d) => d.value > 0);
  const avg = spent.reduce((s, d) => s + d.value, 0) / Math.max(spent.length, 1);
  const peak = billing.daily.reduce((m, d) => (d.value > m.value ? d : m), billing.daily[0]);
  const items = [
    { label: "This month", value: money(billing.monthToDate) },
    { label: "Forecast", value: money(billing.forecast) },
    { label: "Average day", value: money(avg) },
    { label: "Busiest day", value: `${money(peak.value)}, ${new Date(peak.t).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" })}` },
  ];
  return (
    <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-4 border-t border-border pt-4 sm:grid-cols-4">
      {items.map((i) => (
        <div key={i.label}>
          <dt className="text-[12px] text-fg-muted">{i.label}</dt>
          <dd className="num mt-0.5 font-display text-[18px] font-semibold">{i.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Credits left, and each app's spend and AI tokens against its caps. */
function BudgetPanel({ billing, details }: { billing: BillingOverview | null; details: AppDetail[] }) {
  const credits = billing?.credits;
  const capped = details.filter((d) => d.caps.monthlySpend > 0).slice(0, 4);
  return (
    <Panel>
      <PanelHeader title="Budgets" description="Credits, and spend against each app's monthly cap" />
      <div className="flex flex-col gap-5 p-4">
        {credits && credits.granted > 0 && (
          <div>
            <div className="flex items-baseline justify-between gap-3 text-[13px]">
              <span className="font-medium">ShipCube credits</span>
              <span className="num text-fg-muted">
                {money(Math.max(credits.granted - credits.used, 0))} left of {money(credits.granted)}
              </span>
            </div>
            <div className="mt-2">
              <Meter value={credits.used} max={credits.granted} label="Credits used" />
            </div>
          </div>
        )}
        {capped.map((d) => {
          const spent = d.monitoring?.costMonthToDate ?? d.costMonthToDate ?? d.usage?.spend ?? 0;
          return (
            <div key={d.id}>
              <div className="flex items-baseline justify-between gap-3 text-[13px]">
                <Link href={`/apps/${d.id}`} className="truncate font-medium hover:underline">
                  {d.name}
                </Link>
                <span className="num shrink-0 text-fg-muted">
                  {money(spent)} of {money(d.caps.monthlySpend)}
                </span>
              </div>
              <div className="mt-2">
                <Meter value={spent} max={d.caps.monthlySpend} label={`${d.name} spend against cap`} />
              </div>
              {d.caps.llmTokensPerDay && d.usage?.tokensToday !== undefined && (
                <p className="num mt-1.5 text-[12px] text-fg-muted">
                  AI tokens today: {short(d.usage.tokensToday)} of {short(d.caps.llmTokensPerDay)}
                </p>
              )}
            </div>
          );
        })}
        {!capped.length && !(credits && credits.granted > 0) && <p className="text-fg-muted">No caps or credits to show yet.</p>}
      </div>
    </Panel>
  );
}

/** One row per app: health, tier, traffic, p95 latency over the day, error rate, spend against cap. */
function HealthPanel({ apps, details, total }: { apps: AppSummary[]; details: AppDetail[]; total: number }) {
  const byId = new Map(details.map((d) => [d.id, d]));
  return (
    <Panel>
      <PanelHeader
        title="App health"
        description={total > apps.length ? `The ${apps.length} busiest of ${total} apps, last 24 hours` : "Last 24 hours"}
        actions={
          <Link href="/apps" className="inline-flex items-center gap-1 text-[13px] text-accent hover:underline">
            All apps <ArrowRight className="size-3.5" aria-hidden />
          </Link>
        }
      />
      <div className="overflow-x-auto">
        <table className="w-full min-w-[760px]">
          <caption className="sr-only">Health of each app over the last 24 hours</caption>
          <thead>
            <tr className="border-b border-border text-left text-[12px] text-fg-muted">
              <th scope="col" className="px-4 py-2 font-medium">App</th>
              <th scope="col" className="px-4 py-2 font-medium">Status</th>
              <th scope="col" className="px-4 py-2 text-right font-medium">Requests</th>
              <th scope="col" className="px-4 py-2 font-medium">p95 latency</th>
              <th scope="col" className="px-4 py-2 text-right font-medium">Errors</th>
              <th scope="col" className="px-4 py-2 font-medium">Spend against cap</th>
            </tr>
          </thead>
          <tbody>
            {apps.map((a) => {
              const raw = byId.get(a.id)?.monitoring;
              // An app that hasn't served yet has no usable numbers (the API may send NaN or zeros).
              const m = raw && Number.isFinite(raw.p95Ms) && (raw.latency.length > 0 || raw.p95Ms > 0) ? raw : undefined;
              const cap = byId.get(a.id)?.caps.monthlySpend ?? a.spendCap ?? 0;
              const spent = m?.costMonthToDate ?? a.costMonthToDate ?? 0;
              return (
                <tr key={a.id} className="border-b border-border last:border-0">
                  <th scope="row" className="px-4 py-3 text-left font-normal">
                    <Link href={`/apps/${a.id}/monitoring`} className="font-medium hover:underline">
                      {a.name}
                    </Link>
                    <span className="ml-2 align-middle">
                      <TierBadge tier={a.tier} />
                    </span>
                  </th>
                  <td className="px-4 py-3">
                    <HealthPill health={a.health} />
                  </td>
                  <td className="num px-4 py-3 text-right">{a.requests24h !== undefined ? short(a.requests24h) : "-"}</td>
                  <td className="px-4 py-3">
                    {m && m.latency.length > 1 ? (
                      <span className="flex items-center gap-3">
                        <Sparkline values={m.latency.map((p) => p.p95)} label={`${a.name} p95 latency, last 24 hours, now ${count(m.p95Ms)} ms`} />
                        <span className="num text-[13px]">{count(m.p95Ms)} ms</span>
                      </span>
                    ) : m ? (
                      <span className="num text-[13px]">{count(m.p95Ms)} ms</span>
                    ) : (
                      <span className="text-[12px] text-fg-subtle">No metrics yet</span>
                    )}
                  </td>
                  <td className={`num px-4 py-3 text-right ${m && m.errorRate >= 1 ? "font-medium text-critical" : ""}`}>
                    {m ? pct(m.errorRate) : "-"}
                  </td>
                  <td className="px-4 py-3">
                    {cap > 0 ? (
                      <span className="flex items-center gap-3">
                        <span className="w-24">
                          <Meter value={spent} max={cap} label={`${a.name} spend against cap`} />
                        </span>
                        <span className="num text-[12px] text-fg-muted">
                          {money(spent)} / {money(cap)}
                        </span>
                      </span>
                    ) : (
                      <span className="text-[12px] text-fg-subtle">No cap</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

const PARTS = [
  { label: "Compute", color: "var(--chart-1)" },
  { label: "Data", color: "var(--chart-2)" },
  { label: "AI", color: "var(--chart-3)" },
];

/** Where the month's money goes: by category overall, and by app. */
function CostByAppPanel({ billing }: { billing: BillingOverview | null }) {
  const rows = (billing?.byApp ?? [])
    .map((a) => ({ label: a.name, values: [a.compute, a.data, a.llm] }))
    .sort((x, y) => y.values.reduce((s, v) => s + v, 0) - x.values.reduce((s, v) => s + v, 0))
    .slice(0, 6);
  const totals = PARTS.map((p, i) => ({ ...p, value: rows.reduce((s, r) => s + r.values[i], 0) }));
  return (
    <Panel>
      <PanelHeader title="Where the money goes" description="This month, by category and by app" />
      <div className="flex flex-col gap-6 p-4">
        {rows.length ? (
          <>
            <Donut slices={totals} total={money(billing?.monthToDate ?? 0)} caption="This month's spend by category" />
            <StackedBars rows={rows} parts={PARTS} format={money} caption="This month's spend by app and category, US dollars" />
          </>
        ) : (
          <p className="py-6 text-fg-muted">{billing ? "No spend recorded this month." : "Billing isn't available from this API yet."}</p>
        )}
      </div>
    </Panel>
  );
}

/** Requests per app over the last 24 hours, busiest first. */
function TrafficPanel({ apps, details }: { apps: AppSummary[]; details: AppDetail[] }) {
  const rows = apps
    .filter((a) => (a.requests24h ?? 0) > 0)
    .sort((a, b) => (b.requests24h ?? 0) - (a.requests24h ?? 0))
    .slice(0, 6);
  const max = Math.max(...rows.map((r) => r.requests24h ?? 0), 1);
  return (
    <Panel>
      <PanelHeader title="Traffic and errors" description="Requests and failed requests per app, last 24 hours" />
      <div className="flex flex-col gap-6 p-4">
        {rows.length ? (
          <ul className="flex flex-col gap-3">
            {rows.map((a) => (
              <li key={a.id} className="grid grid-cols-[minmax(0,140px)_minmax(0,1fr)_auto] items-center gap-3 text-[13px]">
                <Link href={`/apps/${a.id}`} className="truncate hover:underline">
                  {a.name}
                </Link>
                <span className="h-2.5 overflow-hidden rounded-full bg-surface-2" aria-hidden>
                  <span className="block h-full rounded-full bg-mark" style={{ width: `${((a.requests24h ?? 0) / max) * 100}%` }} />
                </span>
                <span className="num w-16 text-right font-medium">{short(a.requests24h ?? 0)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="py-6 text-fg-muted">No requests in the last 24 hours.</p>
        )}
        <ErrorRates details={details} apps={apps} />
      </div>
    </Panel>
  );
}

/** Error rate per app, worst first. Anything at 1% or more is drawn in the critical colour. */
function ErrorRates({ details, apps }: { details: AppDetail[]; apps: AppSummary[] }) {
  // Request counts come from the apps list; an app with no traffic has no meaningful error rate.
  const served = new Map(apps.map((a) => [a.id, a.requests24h ?? 0]));
  const rows = details
    .filter((d) => d.monitoring && Number.isFinite(d.monitoring.errorRate) && Math.max(d.monitoring.requests ?? 0, served.get(d.id) ?? 0) > 0)
    .map((d) => ({ id: d.id, name: d.name, rate: d.monitoring!.errorRate }))
    .sort((a, b) => b.rate - a.rate)
    .slice(0, 6);
  if (!rows.length) return null;
  return (
    <div className="border-t border-border pt-5">
      <h3 className="text-[13px] font-medium">Error rate</h3>
      <ul className="mt-3 flex flex-col gap-3">
        {rows.map((r) => (
          <li key={r.id} className="grid grid-cols-[minmax(0,140px)_minmax(0,1fr)_auto] items-center gap-3 text-[13px]">
            <Link href={`/apps/${r.id}/monitoring`} className="truncate hover:underline">
              {r.name}
            </Link>
            <span className="h-2.5 overflow-hidden rounded-full bg-surface-2" aria-hidden>
              {/* Scaled to 10%, so small rates still show; anything above fills the bar. */}
              <span
                className={`block h-full rounded-full ${r.rate >= 1 ? "bg-critical" : "bg-mark"}`}
                style={{ width: `${Math.max(Math.min(r.rate / 10, 1) * 100, r.rate > 0 ? 2 : 0)}%` }}
              />
            </span>
            <span className={`num w-16 text-right font-medium ${r.rate >= 1 ? "text-critical" : ""}`}>{pct(r.rate)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function WaitingPanel({ pending, unavailable }: { pending: Approval[]; unavailable: boolean }) {
  return (
    <Panel>
      <PanelHeader title="Waiting for you" description="Agent requests that need a person" />
      {pending.length ? (
        <ul>
          {pending.slice(0, 5).map((a) => (
            <li key={a.id} className="border-b border-border last:border-0">
              <Link href={`/approvals/${a.id}`} className="flex items-start gap-3 px-4 py-3 hover:bg-surface-2">
                <span className="mt-1.5 size-2 shrink-0 rounded-full bg-warning" aria-hidden />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{a.title}</span>
                  <span className="block truncate text-[12px] text-fg-muted">
                    {a.appName}, asked {ago(a.requestedAt)}
                    {a.requestedBy ? ` by ${a.requestedBy.agent}` : ""}
                  </span>
                </span>
                <ArrowRight className="mt-1 size-3.5 shrink-0 text-fg-subtle" aria-hidden />
              </Link>
            </li>
          ))}
        </ul>
      ) : (
        <div className="flex items-center gap-3 px-4 py-6 text-fg-muted">
          <Inbox className="size-4 text-fg-subtle" aria-hidden />
          {unavailable ? "Approvals couldn't be loaded." : "Nothing is waiting. Your agent's risky requests show up here."}
        </div>
      )}
    </Panel>
  );
}

function DeploysPanel({ apps }: { apps: AppSummary[] }) {
  const recent = [...apps].sort((a, b) => b.lastDeployAt.localeCompare(a.lastDeployAt)).slice(0, 5);
  return (
    <Panel>
      <PanelHeader title="Recent deploys" description="Newest first" />
      <ol>
        {recent.map((a) => (
          <li key={a.id} className="flex items-center gap-3 border-b border-border px-4 py-3 last:border-0">
            <Rocket className="size-4 shrink-0 text-fg-subtle" aria-hidden />
            <span className="min-w-0 flex-1">
              <Link href={`/apps/${a.id}`} className="block truncate font-medium hover:underline">
                {a.name}
              </Link>
              <span className="block truncate text-[12px] text-fg-muted">
                {ago(a.lastDeployAt)}
                {a.deployedBy ? ` by ${a.deployedBy}` : ""}
              </span>
            </span>
            <HealthPill health={a.health} />
          </li>
        ))}
      </ol>
    </Panel>
  );
}
