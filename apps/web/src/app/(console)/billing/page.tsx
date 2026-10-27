import type { Metadata } from "next";
import { ExternalLink } from "lucide-react";
import Link from "next/link";
import { Suspense } from "react";
import { CostChart } from "@/components/charts";
import { EmptyState, Meter, PageHeader, Panel, PanelHeader, Stat, StatRow, TableSkeleton, ButtonLink } from "@/components/ui";
import { demoState, getBilling, type DemoState } from "@/lib/api";
import { date, money } from "@/lib/format";

export const metadata: Metadata = { title: "Billing" };

type Props = { searchParams: Promise<Record<string, string | string[] | undefined>> };

export default async function BillingPage({ searchParams }: Props) {
  const state = demoState(await searchParams);
  return (
    <>
      <PageHeader
        title="Billing"
        description="Apps on ShipCube's GCP project draw from your ShipCube credits. Apps on your own GCP project bill to your Google Cloud account; ShipCube shows those costs here but doesn't charge for them."
      />
      <Suspense key={state} fallback={<Panel><TableSkeleton rows={6} cols={5} /></Panel>}>
        <BillingBody state={state} />
      </Suspense>
    </>
  );
}

async function BillingBody({ state }: { state: DemoState }) {
  const b = await getBilling(state);
  const left = b.credits.granted - b.credits.used;
  const rows = b.byApp.map((a) => ({ ...a, total: a.compute + a.data + a.llm })).sort((x, y) => y.total - x.total);
  const onShipcube = rows.filter((r) => r.billing === "shipcube").reduce((s, r) => s + r.total, 0);
  const onGcp = rows.filter((r) => r.billing === "own-gcp").reduce((s, r) => s + r.total, 0);

  return (
    <div className="flex flex-col gap-4">
      <StatRow>
        <Stat
          label="ShipCube credits left"
          value={money(left)}
          note={
            <span className="flex flex-col gap-1.5 pt-0.5">
              <Meter value={b.credits.used} max={b.credits.granted} label="Credits used" />
              <span className="num">{money(b.credits.used)} of {money(b.credits.granted)} used · expires {date(b.credits.expiresAt)}</span>
            </span>
          }
        />
        <Stat label="This month, all apps" value={money(b.monthToDate)} note="October 1–27" />
        <Stat label="From ShipCube credits" value={money(onShipcube)} note="Apps on ShipCube's GCP project" />
        <Stat label="Billed to your GCP" value={money(onGcp)} note={b.gcp.linked ? <span className="font-mono">{b.gcp.projectId}</span> : "No project linked"} />
      </StatRow>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Panel>
          <PanelHeader title="Daily spend from credits" description="October 2026, apps on ShipCube's GCP project" />
          <div className="p-4">
            {b.daily.length ? (
              <CostChart data={b.daily} />
            ) : (
              <p className="py-10 text-center text-fg-muted">No spend this month.</p>
            )}
          </div>
        </Panel>
        <Panel>
          <PanelHeader
            title="Your GCP project"
            actions={<ButtonLink href="/settings/gcp" size="sm">{b.gcp.linked ? "Manage" : "Link project"}</ButtonLink>}
          />
          <div className="flex flex-col gap-3 p-4">
            {b.gcp.linked ? (
              <>
                <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2">
                  <dt className="text-fg-muted">Project</dt>
                  <dd className="font-mono text-[12.5px]">{b.gcp.projectId}</dd>
                  <dt className="text-fg-muted">Billing account</dt>
                  <dd className="font-mono text-[12.5px]">{b.gcp.billingAccount}</dd>
                  <dt className="text-fg-muted">Linked</dt>
                  <dd>{b.gcp.linkedAt && date(b.gcp.linkedAt)}</dd>
                </dl>
                <p className="text-fg-muted">
                  Free-trial credits and committed-use discounts on this account apply to these apps. Google invoices them
                  directly.
                </p>
                <a
                  href={`https://console.cloud.google.com/billing?project=${b.gcp.projectId}`}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 text-accent hover:underline"
                >
                  Open Google Cloud billing <ExternalLink className="size-3" aria-hidden />
                </a>
              </>
            ) : (
              <p className="text-fg-muted">
                Link a GCP project to run apps on your own credits. Until then, every app draws from ShipCube credits.
              </p>
            )}
          </div>
        </Panel>
      </div>

      <Panel>
        <PanelHeader title="By app" description="Month to date" />
        {rows.length === 0 ? (
          <EmptyState title="Nothing billed yet" body="Costs appear here once your agent deploys an app." action={<ButtonLink href="/setup" variant="primary">Connect your agent</ButtonLink>} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-left">
              <thead className="text-[12px] text-fg-muted">
                <tr className="border-b border-border">
                  <th className="px-4 py-2 font-medium">App</th>
                  <th className="px-4 py-2 font-medium">Paid from</th>
                  <th className="px-4 py-2 text-right font-medium">Compute</th>
                  <th className="px-4 py-2 text-right font-medium">Queries</th>
                  <th className="px-4 py-2 text-right font-medium">LLM tokens</th>
                  <th className="px-4 py-2 text-right font-medium">Total</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.appId} className="border-b border-border last:border-0">
                    <td className="px-4 py-2">
                      <Link href={`/apps/${r.appId}/monitoring`} className="font-mono text-[12.5px] hover:text-accent">{r.name}</Link>
                    </td>
                    <td className="px-4 py-2 text-fg-muted">{r.billing === "own-gcp" ? "Your GCP" : "ShipCube credits"}</td>
                    <td className="num px-4 py-2 text-right">{money(r.compute)}</td>
                    <td className="num px-4 py-2 text-right">{money(r.data)}</td>
                    <td className="num px-4 py-2 text-right">{money(r.llm)}</td>
                    <td className="num px-4 py-2 text-right font-medium">{money(r.total)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t border-border-strong">
                  <td className="px-4 py-2 font-medium" colSpan={5}>Total</td>
                  <td className="num px-4 py-2 text-right font-semibold">{money(onShipcube + onGcp)}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
