// Data access for the console. Every function here is async and shaped like the
// control-plane API call it stands in for, so swapping mocks for fetch() is local.

import { apps, billing, providers, tokens } from "./mock-data";
import type { AppDetail, AppSummary, ApiToken, BillingOverview, ProviderConfig } from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    public requestId: string,
  ) {
    super(message);
  }
}

/** Demo switch: append ?state=empty or ?state=error to a console URL to see those states. */
export type DemoState = "empty" | "error" | undefined;

export function demoState(searchParams: Record<string, string | string[] | undefined>): DemoState {
  const s = searchParams.state;
  return s === "empty" || s === "error" ? s : undefined;
}

const LATENCY_MS = 350;
const wait = (ms = LATENCY_MS) => new Promise((r) => setTimeout(r, ms));

function fail(): never {
  throw new ApiError("The control-plane API didn't respond within 10 seconds.", "req_7f3a91c2e0");
}

export async function listApps(state?: DemoState): Promise<AppSummary[]> {
  await wait();
  if (state === "error") fail();
  if (state === "empty") return [];
  return apps.map((a) => ({
    id: a.id,
    name: a.name,
    kind: a.kind,
    tier: a.tier,
    health: a.health,
    url: a.url,
    region: a.region,
    billing: a.billing,
    costMonthToDate: a.costMonthToDate,
    spendCap: a.spendCap,
    requests24h: a.requests24h,
    sharedWith: a.sharedWith,
    lastDeployAt: a.lastDeployAt,
    deployedBy: a.deployedBy,
    aiProvider: a.aiProvider,
  }));
}

export async function getApp(id: string, state?: DemoState): Promise<AppDetail | null> {
  await wait();
  if (state === "error") fail();
  const app = apps.find((a) => a.id === id) ?? null;
  if (app && state === "empty") {
    return { ...app, access: app.access.filter((g) => g.role === "owner"), logs: [] };
  }
  return app;
}

export async function listProviders(): Promise<ProviderConfig[]> {
  await wait(200);
  return providers;
}

export async function listTokens(state?: DemoState): Promise<ApiToken[]> {
  await wait(200);
  if (state === "error") fail();
  if (state === "empty") return [];
  return tokens;
}

export async function getBilling(state?: DemoState): Promise<BillingOverview> {
  await wait();
  if (state === "error") fail();
  if (state === "empty") return { ...billing, gcp: { linked: false }, byApp: [], monthToDate: 0, forecast: 0, daily: [] };
  return billing;
}
