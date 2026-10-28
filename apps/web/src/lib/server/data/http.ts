import "server-only";

// Production data source: the control-plane API (services/api/openapi.yaml). Every call
// goes through lib/server/http.ts. Features whose endpoints don't exist yet throw
// NotConnectedError instead of returning anything made up; see FEATURE_ENDPOINT.

import { ApiError, NotConnectedError } from "@/lib/errors";
import type {
  AccessGrant,
  AppDetail,
  AppSummary,
  Approval,
  ApprovalKind,
  Caps,
  Health,
  LoadEstimate,
  LogLine,
  Monitoring,
  Role,
  TierId,
} from "@/lib/types";
import { api } from "../http";
import { log } from "../log";
import { FEATURE_ENDPOINT, type ConsoleData, type Feature, type PendingApproval } from "./source";

// --- API shapes (services/api/shipcube_api/models.py) ---------------------------------

interface ApiAppSummary { name: string; url: string; tier: string; status: string; updated_at: string }
interface ApiAccess { principal: string; role: string; granted_at: string }
export interface ApiApp extends ApiAppSummary {
  target: string;
  region: string;
  revision: string;
  image: string;
  caps: Record<string, unknown>;
  access: ApiAccess[];
  ai: { provider: string; model: string; key_secret: string | null };
  deleted_at?: string | null;
  restorable_until?: string | null;
  runtime?: Record<string, unknown> | null;
  /** The load estimate of the deploy that created the app (networking, GET /v1/apps/{app}). */
  estimate?: ApiEstimate | null;
}
export interface ApiEstimate {
  estimate_id: string;
  workload_class: string;
  tier: string;
  confidence: number;
  /** A number, or the estimator's {low, high, expected} range. */
  monthly_cost_usd: number | { low?: number; high?: number; expected?: number };
  explained_by: string;
  created_at: string;
  estimated_by?: string;
  reasoning?: string[];
  signals?: { label: string; value: string }[];
}
interface ApiLogs { entries: { timestamp: string; severity: string; message: string; revision?: string | null }[]; next_page_token: string | null }
interface ApiMetrics {
  app: string;
  window: string;
  requests: number;
  error_rate: number;
  latency_ms: { p50: number; p95: number };
  instances: { avg: number; max: number };
  cold_starts: number;
  cpu_utilization: number;
  memory_utilization: number;
  cost_usd: Record<string, number>;
  caps_hit: Record<string, number>;
  data_freshness_minutes: number | null;
}
export interface ApiOperation {
  id: string;
  kind: string;
  app: string | null;
  status: "awaiting_approval" | "queued" | "running" | "succeeded" | "failed" | "cancelled";
  done: boolean;
  error?: { code: string; message: string } | null;
  approval_url?: string | null;
  approval_reasons?: string[];
  approval_actions?: ApprovalKind[];
  approval_expires_at?: string | null;
  created_at: string;
  updated_at: string;
}

// --- mapping -------------------------------------------------------------------------

const TIERS = new Set(["T0", "T1", "T2", "T3", "T4"]);
export const tierOf = (t: string): TierId => (TIERS.has(t) ? (t as TierId) : "T1");

export function healthOf(status: string): Health {
  const s = status.toLowerCase();
  if (["ready", "running", "serving", "healthy", "succeeded"].includes(s)) return "healthy";
  if (["deploying", "building", "queued", "pending", "awaiting_approval"].includes(s)) return "deploying";
  if (["degraded", "warning"].includes(s)) return "degraded";
  if (["failed", "error", "down", "stopped"].includes(s)) return "down";
  return "unknown";
}

/** "512Mi" ↔ "512 MiB" */
export const memoryToUi = (m: unknown) => (typeof m === "string" ? m.replace(/^(\d+)(Mi|Gi)$/, "$1 $2B") : "512 MiB");
export const memoryToApi = (m: string) => m.replace(/^(\d+)\s*(Mi|Gi)B$/, "$1$2");

/** Where an app runs. The local stack reports target "local", or the local backend in runtime. */
export function placementOf(a: Pick<ApiApp, "target" | "region" | "runtime">): Pick<AppDetail, "runsLocally" | "billing" | "region"> {
  const target = a.target ?? "";
  if (target === "local" || a.runtime?.backend === "local") return { runsLocally: true };
  return { billing: target.includes("linked") ? "own-gcp" : "shipcube", region: a.region };
}

/** Resources come from the app's caps, which deploy set from the estimate's sizing. */
export function estimateFromApi(e: ApiEstimate, caps: Caps, cpu?: unknown): LoadEstimate {
  const finite = (n: unknown): n is number => typeof n === "number" && Number.isFinite(n);
  const cost = e.monthly_cost_usd;
  const range = typeof cost === "object" && cost && finite(cost.low) && finite(cost.high) ? { low: cost.low, high: cost.high } : undefined;
  return {
    // The estimator writes classes with underscores (bursty_interactive); the console uses hyphens.
    workloadClass: String(e.workload_class ?? "").replaceAll("_", "-") as LoadEstimate["workloadClass"],
    confidence: finite(e.confidence) ? e.confidence : 0,
    recommendedTier: tierOf(e.tier),
    cpu: `${typeof cpu === "string" || typeof cpu === "number" ? cpu : 1} vCPU`,
    memory: caps.memory,
    minInstances: 0,
    maxInstances: caps.maxInstances,
    monthlyCost: finite(cost) ? cost : typeof cost === "object" && cost && finite(cost.expected) ? cost.expected : undefined,
    monthlyCostRange: range,
    reasoning: e.reasoning ?? [],
    signals: e.signals ?? [],
    estimatedBy: e.estimated_by ?? "workload classifier",
    explainedBy: e.explained_by,
    estimatedAt: e.created_at,
    overridden: false,
  };
}

export function capsFromApi(c: Record<string, unknown>): Caps {
  const n = (k: string, d = 0) => (typeof c[k] === "number" ? (c[k] as number) : d);
  return {
    monthlySpend: n("monthly_spend_usd"),
    requestsPerMinute: n("requests_per_minute"),
    concurrency: n("concurrency", 80),
    maxInstances: n("max_instances", 1),
    memory: memoryToUi(c.memory),
  };
}

export function capsToApi(c: Partial<Caps>) {
  return {
    monthly_spend_usd: c.monthlySpend,
    requests_per_minute: c.requestsPerMinute,
    concurrency: c.concurrency,
    max_instances: c.maxInstances,
    memory: c.memory ? memoryToApi(c.memory) : undefined,
  };
}

export function grantFromApi(a: ApiAccess, ownerEmail?: string): AccessGrant {
  const [kind, ...rest] = a.principal.split(":");
  const principal = rest.join(":") || a.principal;
  const role: Role = principal === ownerEmail || a.role === "owner" ? "owner" : a.role === "editor" ? "editor" : "viewer";
  return {
    id: a.principal,
    kind: kind === "group" ? "group" : kind === "domain" ? "domain" : "user",
    principal,
    role,
    addedAt: a.granted_at,
  };
}

export function logFromApi(e: ApiLogs["entries"][number], i: number): LogLine {
  const sev = e.severity.toUpperCase();
  return {
    id: `${e.timestamp}-${i}`,
    ts: e.timestamp,
    level: sev === "ERROR" || sev === "CRITICAL" ? "error" : sev === "WARNING" ? "warn" : "info",
    revision: e.revision ?? undefined,
    message: e.message,
  };
}

const COST_LABEL: Record<string, string> = { compute: "Compute", ai_tokens: "AI tokens", warehouse: "Warehouse queries", egress: "Network egress" };

export function monitoringFromApi(m: ApiMetrics, health: Health): Monitoring {
  const errorPct = m.error_rate <= 1 ? m.error_rate * 100 : m.error_rate;
  return {
    window: m.window,
    health,
    requests: m.requests,
    p50Ms: Math.round(m.latency_ms.p50),
    p95Ms: Math.round(m.latency_ms.p95),
    errorRate: +errorPct.toFixed(2),
    coldStarts24h: m.cold_starts,
    freshness: m.data_freshness_minutes == null ? "unknown" : m.data_freshness_minutes <= 60 ? "fresh" : "stale",
    sourceUpdatedAt:
      m.data_freshness_minutes == null ? undefined : new Date(Date.now() - m.data_freshness_minutes * 60_000).toISOString(),
    costBreakdown: Object.entries(m.cost_usd)
      .filter(([k]) => k !== "total" && k !== "month_to_date")
      .map(([key, value]) => ({ label: COST_LABEL[key] ?? key.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase()), value })),
    costWindow: `last ${m.window}`,
    costMonthToDate: m.cost_usd.month_to_date,
    latency: [],
    dailyCost: [],
  };
}

const KIND_TITLE: Record<ApprovalKind, (app: string) => string> = {
  first_linked_deploy: (a) => `Deploy ${a} to your GCP project`,
  cap_raise: (a) => `Raise caps on ${a}`,
  domain_grant: (a) => `Share ${a} with a whole domain`,
  delete_app: (a) => `Delete ${a}`,
};
const KIND_EFFECTS: Record<ApprovalKind, string[]> = {
  first_linked_deploy: ["ShipCube builds and deploys the app into your linked project, billed to its billing account.", "The agent's deploy continues and reports the URL."],
  cap_raise: ["The new caps apply immediately."],
  domain_grant: ["Anyone who signs in with a Google account on that domain can open the app."],
  delete_app: ["The URL stops working and nobody can open the app.", "You can restore it from Apps for 7 days. After that ShipCube removes the service and its image."],
};
const KIND_TOOL: Record<ApprovalKind, string> = {
  first_linked_deploy: "deploy_app",
  cap_raise: "set_caps",
  domain_grant: "set_access",
  delete_app: "delete_app",
};

export function approvalFromOperation(op: ApiOperation): Approval {
  const kind: ApprovalKind = op.approval_actions?.[0] ?? (op.kind === "delete" ? "delete_app" : op.kind === "set_caps" ? "cap_raise" : op.kind === "set_access" ? "domain_grant" : "first_linked_deploy");
  const app = op.app ?? "an app";
  const status: Approval["status"] =
    op.status === "awaiting_approval"
      ? "pending"
      : op.status === "cancelled" && op.error?.code === "approval_rejected"
        ? "rejected"
        : op.status === "cancelled" && op.error?.code === "approval_expired"
          ? "expired"
          : "approved";
  return {
    id: op.id,
    kind,
    status,
    title: KIND_TITLE[kind](app),
    // A first deploy's app doesn't exist until the deploy runs, so there's nothing to link to.
    appId: kind === "first_linked_deploy" ? undefined : (op.app ?? undefined),
    appName: app,
    requestedAt: op.created_at,
    expiresAt: op.approval_expires_at ?? op.updated_at,
    reason: (op.approval_reasons ?? []).join(" ") || "This action needs a person to approve it.",
    effects: KIND_EFFECTS[kind],
    impact: [],
    tool: { name: KIND_TOOL[kind], args: {} },
    decision: status === "pending" ? undefined : { at: op.updated_at, note: status === "rejected" ? op.error?.message : undefined },
  };
}

function pending(op?: ApiOperation | null): PendingApproval | undefined {
  if (!op || op.status !== "awaiting_approval") return undefined;
  return { id: op.id, url: `/approvals/${op.id}`, reasons: op.approval_reasons ?? [] };
}

async function orNull<T>(p: Promise<T>): Promise<T | null> {
  try {
    return await p;
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

/** Secondary data (logs, metrics) shouldn't take the whole page down; log and carry on. */
async function optional<T>(what: string, p: Promise<T>): Promise<T | undefined> {
  try {
    return await p;
  } catch (e) {
    log("WARNING", `${what} unavailable`, { error: String(e), code: e instanceof ApiError ? e.code : undefined });
    return undefined;
  }
}

const notConnected = (f: Feature) => new NotConnectedError(FEATURE_ENDPOINT[f].name, FEATURE_ENDPOINT[f].endpoint);
const AVAILABLE = new Set<Feature>([]);
const enc = encodeURIComponent;

export const httpData: ConsoleData = {
  kind: "http",
  supports: (f) => AVAILABLE.has(f),

  async listApps() {
    const { apps } = await api<{ apps: ApiAppSummary[] }>("/v1/apps");
    return apps.map(
      (a): AppSummary => ({ id: a.name, name: a.name, tier: tierOf(a.tier), health: healthOf(a.status), url: a.url, lastDeployAt: a.updated_at }),
    );
  },

  async getApp(id) {
    const a = await orNull(api<ApiApp>(`/v1/apps/${enc(id)}`));
    if (!a) return null;
    const health = a.deleted_at ? "down" : healthOf(a.status);
    const [logs, metrics] = await Promise.all([
      optional("logs", api<ApiLogs>(`/v1/apps/${enc(id)}/logs`, { query: { limit: 200 } })),
      optional("metrics", api<ApiMetrics>(`/v1/apps/${enc(id)}/metrics`, { query: { window: "24h" } })),
    ]);
    const caps = capsFromApi(a.caps);
    const detail: AppDetail = {
      id: a.name,
      name: a.name,
      tier: tierOf(a.tier),
      health,
      url: a.url,
      ...placementOf(a),
      estimate: a.estimate ? estimateFromApi(a.estimate, caps, a.caps.cpu) : undefined,
      revision: a.revision,
      lastDeployAt: a.updated_at,
      aiProvider: a.ai.provider === "meta" ? `Muse (${a.ai.model})` : `${a.ai.provider} (${a.ai.model})`,
      spendCap: caps.monthlySpend,
      caps,
      access: a.access.map((g) => grantFromApi(g)),
      sharedWith: a.access.length,
      logs: (logs?.entries ?? []).map(logFromApi),
      monitoring: metrics ? monitoringFromApi(metrics, health) : undefined,
      deletedAt: a.deleted_at ?? undefined,
      restorableUntil: a.restorable_until ?? undefined,
    };
    return detail;
  },

  async saveCaps(appId, caps, idempotencyKey) {
    const r = await api<{ caps: Record<string, unknown>; operation?: ApiOperation | null }>(`/v1/apps/${enc(appId)}/caps`, {
      method: "PATCH",
      body: capsToApi(caps),
      idempotencyKey,
    });
    return { caps: capsFromApi(r.caps), approval: pending(r.operation) };
  },

  async changeAccess(appId, change, idempotencyKey) {
    const r = await api<{ access: ApiAccess[]; operation?: ApiOperation | null }>(`/v1/apps/${enc(appId)}/access`, {
      method: "PATCH",
      body: { add: change.add ?? [], remove: change.remove ?? [] },
      idempotencyKey,
    });
    return { access: r.access.map((g) => grantFromApi(g)), approval: pending(r.operation) };
  },

  async restoreApp(appId) {
    await api(`/v1/apps/${enc(appId)}/restore`, { method: "POST" });
  },

  async listApprovals() {
    throw notConnected("approvals_inbox");
  },

  async getApproval(id) {
    const op = await orNull(api<ApiOperation>(`/v1/operations/${enc(id)}`));
    return op ? approvalFromOperation(op) : null;
  },

  async decideApproval(id, decision, comment) {
    const op = await api<ApiOperation>(`/v1/operations/${enc(id)}/${decision}`, {
      method: "POST",
      body: comment ? { comment } : {},
      // Approving twice is harmless: the API answers 409 for a decided operation.
      idempotencyKey: `${decision}-${id}`,
    });
    return approvalFromOperation(op);
  },

  async pendingApprovalCount() {
    return null;
  },

  async listTokens() {
    throw notConnected("tokens");
  },
  async createToken() {
    throw notConnected("tokens");
  },
  async revokeToken() {
    throw notConnected("tokens");
  },
  async listProviders() {
    throw notConnected("providers");
  },
  async saveProviderKey() {
    throw notConnected("providers");
  },
  async removeProviderKey() {
    throw notConnected("providers");
  },
  async getBilling() {
    throw notConnected("billing");
  },
  async linkGcpProject() {
    throw notConnected("gcp_link");
  },
  async getWarehouse() {
    throw notConnected("data");
  },
  async connectWarehouse() {
    throw notConnected("data");
  },
  async listCubes() {
    throw notConnected("data");
  },
  async getCube() {
    throw notConnected("data");
  },
  async listMigrations() {
    throw notConnected("data");
  },
  async getMigration() {
    throw notConnected("data");
  },
  async decideMigration() {
    throw notConnected("data");
  },
};
