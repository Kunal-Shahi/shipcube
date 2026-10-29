import "server-only";

// What server components read. Each call goes to the data source chosen at startup:
// the control-plane API, or typed mocks when DEV_MODE=1 (lib/server/data).

import { mockDataEnabled } from "./server/config";
import { data, type DemoState, type Feature } from "./server/data";

export type { DemoState, Feature };

/** DEV_MODE only: ?state=empty or ?state=error previews those states. Ignored in production. */
export function demoState(searchParams: Record<string, string | string[] | undefined>): DemoState {
  if (!mockDataEnabled()) return undefined;
  const s = searchParams.state;
  return s === "empty" || s === "error" ? s : undefined;
}

export async function supports(feature: Feature) {
  return (await data()).supports(feature);
}

export const listApps = async (s?: DemoState) => (await data()).listApps(s);
export const getApp = async (id: string, s?: DemoState) => (await data()).getApp(id, s);
export const listApprovals = async (s?: DemoState) => (await data()).listApprovals(s);
export const getApproval = async (id: string) => (await data()).getApproval(id);
export const pendingApprovalCount = async () => (await data()).pendingApprovalCount();
export const listTokens = async (s?: DemoState) => (await data()).listTokens(s);
export const listProviders = async () => (await data()).listProviders();
export const getBilling = async (s?: DemoState) => (await data()).getBilling(s);
export const getWarehouse = async (s?: DemoState) => (await data()).getWarehouse(s);
export const listCubes = async (s?: DemoState) => (await data()).listCubes(s);
export const getCube = async (name: string) => (await data()).getCube(name);
export const listMigrations = async (s?: DemoState) => (await data()).listMigrations(s);
export const getMigration = async (id: string) => (await data()).getMigration(id);

/** This workspace's deployer service account, which the link script grants roles to, and its linked project. */
export const getGcpLink = async () => (await data()).getGcpLink();
