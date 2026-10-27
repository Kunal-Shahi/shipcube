import "server-only";

// DEV_MODE data source: typed mock data held in memory, so the console can be run and
// reviewed without the control-plane API. lib/server/data/index.ts refuses to load this
// module unless DEV_MODE=1, and config() refuses DEV_MODE on Cloud Run.

import { ApiError } from "@/lib/errors";
import { approvals } from "@/lib/mock-approvals";
import { apps, billing, providers, tokens } from "@/lib/mock-data";
import { cubes, migrations, warehouse } from "@/lib/mock-data-layer";
import type { AccessGrant, AppSummary, Approval, GcpLink } from "@/lib/types";
import type { ConsoleData, DemoState } from "./source";

const LATENCY_MS = 350;
const wait = (ms = LATENCY_MS) => new Promise((r) => setTimeout(r, ms));

function fail(): never {
  throw new ApiError("unavailable", "The control-plane API didn't respond within 10 seconds.", 503, "req_dev_7f3a91c2");
}

const DEFAULT_SPEND_CAP = 50;
const kindOf = (p: string): AccessGrant["kind"] => (p.startsWith("group:") ? "group" : p.startsWith("domain:") ? "domain" : "user");
const bare = (p: string) => p.replace(/^(user|group|domain):/, "");

function pendingApproval(kind: Approval["kind"], app: { id: string; name: string }, title: string, reason: string, args: Record<string, unknown>, toolName: string) {
  const id = `apr_dev_${Math.random().toString(36).slice(2, 8)}`;
  approvals.unshift({
    id,
    kind,
    status: "pending",
    title,
    appId: app.id,
    appName: app.name,
    requestedBy: { agent: "Console", tokenName: "console session", user: "you" },
    requestedAt: new Date().toISOString(),
    expiresAt: new Date(Date.now() + 72 * 3600_000).toISOString(),
    reason,
    effects: ["The change applies as soon as it's approved."],
    impact: [],
    tool: { name: toolName, args },
  });
  return { id, url: `/approvals/${id}`, reasons: [reason] };
}

export const mockData: ConsoleData = {
  kind: "mock",
  supports: () => true,

  async listApps(state?: DemoState) {
    await wait();
    if (state === "error") fail();
    if (state === "empty") return [];
    return apps.map(
      (a): AppSummary => ({
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
        deletedAt: a.deletedAt,
      }),
    );
  },

  async getApp(id, state) {
    await wait();
    if (state === "error") fail();
    const app = apps.find((a) => a.id === id) ?? null;
    if (app && state === "empty") return { ...app, access: app.access.filter((g) => g.role === "owner"), logs: [], monitoring: undefined };
    return app;
  },

  async saveCaps(appId, caps) {
    await wait(500);
    const app = apps.find((a) => a.id === appId);
    if (!app) throw new ApiError("not_found", `No app ${appId}.`, 404, "");
    if (caps.monthlySpend !== undefined && caps.monthlySpend > Math.max(DEFAULT_SPEND_CAP, app.caps.monthlySpend)) {
      const approval = pendingApproval(
        "cap_raise", app, `Raise the monthly spend cap on ${app.name}`,
        `$${caps.monthlySpend} is above the workspace default of $${DEFAULT_SPEND_CAP} per month.`,
        { app: app.name, monthly_spend_usd: caps.monthlySpend }, "set_caps",
      );
      return { caps: app.caps, approval };
    }
    Object.assign(app.caps, caps);
    if (caps.monthlySpend !== undefined) app.spendCap = caps.monthlySpend;
    return { caps: app.caps };
  },

  async changeAccess(appId, change) {
    await wait(400);
    const app = apps.find((a) => a.id === appId);
    if (!app) throw new ApiError("not_found", `No app ${appId}.`, 404, "");
    for (const p of change.remove ?? []) {
      if (app.access.some((g) => g.role === "owner" && g.principal === bare(p))) throw new ApiError("invalid_argument", "You can't remove the owner.", 400, "");
      app.access = app.access.filter((g) => g.principal !== bare(p));
    }
    let approval;
    for (const g of change.add ?? []) {
      if (kindOf(g.principal) === "domain") {
        approval = pendingApproval(
          "domain_grant", app, `Share ${app.name} with everyone at ${bare(g.principal)}`,
          "Domain grants let anyone on that domain open the app.",
          { app: app.name, add: [g.principal], role: g.role }, "set_access",
        );
        continue;
      }
      const existing = app.access.find((x) => x.principal === bare(g.principal));
      if (existing) existing.role = g.role;
      else app.access.push({ id: crypto.randomUUID(), kind: kindOf(g.principal), principal: bare(g.principal), role: g.role, addedBy: "you", addedAt: new Date().toISOString() });
    }
    app.sharedWith = app.access.length;
    return { access: app.access, approval };
  },

  async restoreApp(appId) {
    await wait(600);
    const app = apps.find((a) => a.id === appId);
    if (!app?.deletedAt) throw new ApiError("gone", "This app can no longer be restored.", 410, "");
    app.deletedAt = undefined;
  },

  async listApprovals(state) {
    await wait(250);
    if (state === "error") fail();
    if (state === "empty") return approvals.filter((a) => a.status !== "pending");
    return approvals;
  },

  async getApproval(id) {
    await wait(250);
    return approvals.find((a) => a.id === id) ?? null;
  },

  async decideApproval(id, decision, comment) {
    await wait(600);
    const a = approvals.find((x) => x.id === id);
    if (!a) return null;
    if (a.status !== "pending") throw new ApiError("conflict", `This request was already ${a.status}.`, 409, "");
    a.status = decision === "approve" ? "approved" : "rejected";
    a.decision = { by: "you", at: new Date().toISOString(), note: comment || undefined };
    return a;
  },

  async pendingApprovalCount() {
    return approvals.filter((a) => a.status === "pending").length;
  },

  async listTokens(state) {
    await wait(200);
    if (state === "error") fail();
    if (state === "empty") return [];
    return tokens;
  },

  async createToken({ name, scopes, days }) {
    await wait(450);
    const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
    const pick = (n: number) => Array.from(crypto.getRandomValues(new Uint8Array(n)), (b) => alphabet[b % alphabet.length]).join("");
    const id = pick(8);
    const secret = `sc_pat_${id}_${pick(43)}`;
    const token = {
      id: `tok_${id}`,
      name,
      prefix: `sc_pat_${id}`,
      scopes,
      createdAt: new Date().toISOString(),
      expiresAt: new Date(Date.now() + days * 86_400_000).toISOString(),
      lastUsedAt: null,
    };
    tokens.unshift(token);
    return { token, secret };
  },

  async revokeToken(id) {
    await wait(300);
    const i = tokens.findIndex((t) => t.id === id);
    if (i >= 0) tokens.splice(i, 1);
  },

  async listProviders() {
    await wait(200);
    return providers;
  },

  async saveProviderKey(id, { key, baseUrl, model }) {
    await wait(700);
    const p = providers.find((x) => x.id === id);
    if (!p) throw new ApiError("not_found", "Unknown provider.", 404, "");
    const prefix = id === "anthropic" ? "sk-ant-" : id === "openai" ? "sk-" : null;
    if (prefix && !key.startsWith(prefix)) {
      throw new ApiError("invalid_argument", `${p.label} rejected this key (401 Unauthorized). ${p.label} keys start with "${prefix}". Check you copied the whole key.`, 400, "");
    }
    Object.assign(p, { connected: true, keyHint: `${key.slice(0, 3)}…${key.slice(-4)}`, baseUrl, defaultModel: model, addedAt: new Date().toISOString() });
    return p;
  },

  async removeProviderKey(id) {
    await wait(300);
    const p = providers.find((x) => x.id === id);
    if (p) Object.assign(p, { connected: false, keyHint: undefined });
  },

  async getBilling(state) {
    await wait();
    if (state === "error") fail();
    if (state === "empty") return { ...billing, gcp: { linked: false }, byApp: [], monthToDate: 0, forecast: 0, daily: [] };
    return billing;
  },

  async linkGcpProject({ projectId, region }) {
    await wait(900);
    if (!/^[a-z][a-z0-9-]{4,28}[a-z0-9]$/.test(projectId)) {
      throw new ApiError("invalid_argument", "That isn't a valid project ID. It's 6–30 lowercase letters, digits or hyphens, like acme-analytics-prod.", 400, "");
    }
    const link: GcpLink = {
      linked: true,
      projectId,
      region,
      linkedAt: new Date().toISOString(),
      serviceAccount: `ws-01jb8k-deployer@shipcube-control-ks.iam.gserviceaccount.com`,
    };
    billing.gcp = link;
    return link;
  },

  async getWarehouse(state) {
    await wait(250);
    if (state === "error") fail();
    if (state === "empty") return { connected: false, modelVersion: 0 };
    return warehouse;
  },

  async connectWarehouse(input, save) {
    await wait(900);
    if (input.kind === "bigquery") {
      let ok = false;
      try {
        ok = JSON.parse(input.serviceAccountKey)?.type === "service_account";
      } catch {}
      if (!ok) {
        throw new ApiError("invalid_argument", "That isn't a service account key. In Google Cloud, open IAM → Service accounts → Keys → Add key → JSON, and paste the whole file.", 400, "");
      }
    } else if (!input.host || !input.user) {
      throw new ApiError("invalid_argument", "Enter the host and user. ShipCube couldn't open a connection without them.", 400, "");
    }
    if (save) {
      Object.assign(warehouse, { connected: true, kind: input.kind, connectedAt: new Date().toISOString(), connectedBy: "you" });
    }
    return { tables: 9, schemas: 2, warehouse: save ? warehouse : undefined };
  },

  async listCubes(state) {
    await wait();
    if (state === "error") fail();
    if (state === "empty") return [];
    return cubes;
  },

  async getCube(name) {
    await wait(250);
    return cubes.find((c) => c.name === name) ?? null;
  },

  async listMigrations(state) {
    await wait();
    if (state === "error") fail();
    if (state === "empty") return migrations.filter((m) => m.status !== "proposed");
    return migrations;
  },

  async getMigration(id) {
    await wait(250);
    return migrations.find((m) => m.id === id) ?? null;
  },

  async decideMigration(id, decision, { allowBreaking, note }) {
    await wait(800);
    const m = migrations.find((x) => x.id === id);
    if (!m) throw new ApiError("not_found", `No migration ${id}.`, 404, "");
    if (m.status !== "proposed") throw new ApiError("conflict", `${id} is already ${m.status}.`, 409, "");
    if (decision === "reject") {
      m.status = "rejected";
      m.rejected = { at: new Date().toISOString(), by: "you", note };
      return m;
    }
    if (m.impact.breaks.length && !allowBreaking) {
      throw new ApiError("invalid_argument", "This migration breaks saved queries. Confirm that you want to apply it anyway.", 400, "");
    }
    warehouse.modelVersion += 1;
    m.status = "applied";
    m.applied = { version: warehouse.modelVersion, at: new Date().toISOString(), by: "you" };
    return m;
  },
};
