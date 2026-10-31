import "server-only";

// Production data source: the control-plane API (services/api/openapi.yaml). Every call
// goes through lib/server/http.ts. AVAILABLE lists the features this API serves; a page whose
// feature isn't listed shows "not available yet" (FEATURE_ENDPOINT names the endpoint it needs).

import { ApiError } from "@/lib/errors";
import type {
  AccessGrant,
  ApiToken,
  AppDetail,
  AppSummary,
  BillingOverview,
  Approval,
  ApprovalKind,
  Caps,
  Cube,
  GcpLink,
  Health,
  LoadEstimate,
  LogLine,
  Migration,
  Monitoring,
  ProviderConfig,
  Role,
  TierId,
  Warehouse,
  AccountStatus,
  WaitlistPage,
  WaitlistRequest,
  WaitlistState,
} from "@/lib/types";
import { api } from "../http";
import { log } from "../log";
import { type ConsoleData, type Feature, type PendingApproval, type WarehouseInput } from "./source";

// --- API shapes (services/api/shipcube_api/models.py) ---------------------------------

interface ApiAppSummary {
  name: string;
  url: string;
  tier: string;
  status: string;
  updated_at: string;
  /** shipcube, local, or a linked project id. */
  target?: string;
  deleted_at?: string | null;
  restorable_until?: string | null;
}
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
  /** Set while a fresh IAP grant may not apply yet. */
  access_note?: string | null;
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

/** Where an app runs. The local stack reports target or region "local", or the local backend in runtime; a
 * linked project's apps have the link's id as their target. */
export function placementOf(a: Pick<ApiApp, "target" | "region" | "runtime">): Pick<AppDetail, "runsLocally" | "billing" | "region"> {
  const target = a.target ?? "";
  if (target === "local" || a.region === "local" || a.runtime?.backend === "local") return { runsLocally: true };
  return { billing: target === "shipcube" ? "shipcube" : "own-gcp", region: a.region };
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

const AVAILABLE = new Set<Feature>(["tokens", "data", "approvals_inbox", "providers", "gcp_link", "billing", "deleted_apps", "waitlist"]);

interface ApiWaitlistRequest {
  id: string;
  email: string;
  name: string;
  use_case: string;
  status: Exclude<WaitlistState, "none">;
  created_at: string;
  decided_at: string | null;
  decided_by: string | null;
  workspace_id?: string | null;
}

export const waitlistFromApi = (r: ApiWaitlistRequest): WaitlistRequest => ({
  id: r.id,
  email: r.email,
  name: r.name,
  useCase: r.use_case,
  status: r.status,
  createdAt: r.created_at,
  decidedAt: r.decided_at ?? undefined,
  decidedBy: r.decided_by ?? undefined,
  workspaceId: r.workspace_id ?? undefined,
});

type ApiWaitlistMe = { email: string; status: WaitlistState; requested_at: string | null; platform_admin: boolean };

/**
 * A verified console session without a workspace is refused by /v1/me: 403 `waitlisted` or
 * `no_access` (older APIs: 401). /v1/waitlist/me then gives the whole state and whether the person
 * is a platform admin anyway. Anything else is the API failing, and is thrown: a member must never
 * be sent to the waitlist because the API blipped.
 */
export async function accountStatusFrom(
  me: () => Promise<{ platform_admin?: boolean }>,
  waitlistMe: () => Promise<ApiWaitlistMe>,
): Promise<AccountStatus> {
  let refusal: string;
  try {
    const m = await me();
    return { member: true, platformAdmin: m.platform_admin === true };
  } catch (e) {
    if (!(e instanceof ApiError && ["unauthenticated", "waitlisted", "no_access"].includes(e.code))) throw e;
    refusal = e.code;
  }
  try {
    const w = await waitlistMe();
    return { member: false, platformAdmin: w.platform_admin === true, waitlist: w.status, requestedAt: w.requested_at ?? undefined };
  } catch (e) {
    // No /v1/waitlist/me: the refusal itself says enough.
    if (e instanceof ApiError && e.code === "not_found") {
      return { member: false, platformAdmin: false, waitlist: refusal === "waitlisted" ? "pending" : "none" };
    }
    throw e;
  }
}

export interface ApiGcpLink {
  id: string;
  project_id: string;
  region: string;
  status: "active" | "broken";
  deployer_sa: string;
  missing_permissions: string[];
  linked_at: string | null;
  verified_at: string | null;
}

export const linkFromApi = (l: ApiGcpLink): GcpLink => ({
  linked: l.status === "active",
  projectId: l.project_id,
  region: l.region,
  linkedAt: l.verified_at ?? l.linked_at ?? undefined,
  serviceAccount: l.deployer_sa,
  status: l.status,
  missingPermissions: l.missing_permissions,
});

interface ApiBilling {
  credits: { granted_usd: number; used_usd: number; expires_at: string | null };
  month_to_date_usd: number;
  forecast_usd: number;
  by_app: { app: string; billing: "shipcube" | "own-gcp"; compute_usd: number; data_usd: number; llm_usd: number }[];
  daily: { date: string; cost_usd: number }[];
  gcp: ApiGcpLink | null;
  source: string;
}

export const billingFromApi = (b: ApiBilling): BillingOverview => ({
  credits: { granted: b.credits.granted_usd, used: b.credits.used_usd, expiresAt: b.credits.expires_at ?? undefined },
  gcp: b.gcp ? linkFromApi(b.gcp) : { linked: false },
  monthToDate: b.month_to_date_usd,
  forecast: b.forecast_usd,
  byApp: b.by_app.map((a) => ({ appId: a.app, name: a.app, billing: a.billing, compute: a.compute_usd, data: a.data_usd, llm: a.llm_usd })),
  daily: b.daily.map((d) => ({ t: d.date, value: d.cost_usd })),
});

/** A list row, including soft-deleted apps (the "Recently deleted" section restores them). */
export const summaryFromApi = (a: ApiAppSummary): AppSummary => ({
  id: a.name,
  name: a.name,
  tier: tierOf(a.tier),
  health: a.deleted_at ? "down" : healthOf(a.status),
  url: a.url,
  lastDeployAt: a.updated_at,
  billing: !a.target || a.target === "local" ? undefined : a.target === "shipcube" ? "shipcube" : "own-gcp",
  deletedAt: a.deleted_at ?? undefined,
});

interface ApiTokenRow {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  created_at: string;
  expires_at: string;
  last_used_at: string | null;
}

interface ApiProvider {
  id: ProviderConfig["id"];
  label: string;
  description: string;
  connected: boolean;
  default: boolean;
  key_hint: string | null;
  base_url: string | null;
  model: string | null;
  added_at: string | null;
}

// The API never returns a stored key, only its last 4 characters (key_hint).
export const providerFromApi = (p: ApiProvider): ProviderConfig => ({
  id: p.id,
  label: p.label,
  description: p.description,
  connected: p.connected,
  isDefault: p.default,
  keyHint: p.key_hint ?? undefined,
  baseUrl: p.base_url ?? undefined,
  defaultModel: p.model ?? undefined,
  addedAt: p.added_at ?? undefined,
});

const tokenFromApi = (t: ApiTokenRow): ApiToken => ({
  id: t.id,
  name: t.name,
  prefix: t.prefix,
  scopes: t.scopes,
  createdAt: t.created_at,
  expiresAt: t.expires_at,
  lastUsedAt: t.last_used_at,
});
// /v1/data/* is served by shipcube-data's router (shipcube_data/http.py). Its bodies are snake_case like the rest of
// /v1 and map one-to-one onto lib/types; `ops` are the migration's own JSON and are passed through untouched.
const camelKey = (k: string) => k.replace(/_([a-z0-9])/g, (_, c: string) => c.toUpperCase());

export function camelize<T>(v: unknown): T {
  if (Array.isArray(v)) return v.map((x) => camelize(x)) as T;
  if (v === null || typeof v !== "object") return v as T;
  return Object.fromEntries(
    Object.entries(v as Record<string, unknown>).map(([k, x]) => [camelKey(k), k === "ops" ? x : camelize(x)]),
  ) as T;
}

// Local stacks run a DuckDB warehouse, which isn't a WarehouseKind the form offers; show it as a detail instead.
function warehouseFromApi(raw: unknown): Warehouse {
  const w = camelize<Warehouse & { kind?: string }>(raw);
  if (!w.kind || ["bigquery", "postgres", "clickhouse"].includes(w.kind)) return w;
  return { ...w, kind: undefined, details: [{ label: "Engine", value: `${w.kind} (local development)` }, ...(w.details ?? [])] };
}

function warehouseBody(input: WarehouseInput) {
  return input.kind === "bigquery"
    ? { kind: input.kind, project: input.project, location: input.location, service_account_key: input.serviceAccountKey }
    : { kind: input.kind, host: input.host, port: input.port, database: input.database, user: input.user, password: input.password, ssl: input.ssl };
}
const enc = encodeURIComponent;

export const httpData: ConsoleData = {
  kind: "http",
  supports: (f) => AVAILABLE.has(f),

  async listApps() {
    const { apps } = await api<{ apps: ApiAppSummary[] }>("/v1/apps", { query: { include_deleted: "true" } });
    return apps.map(summaryFromApi);
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
      accessNote: a.access_note ?? undefined,
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
    // Pending ones, plus recent operations a person decided (they keep approval_actions).
    const { operations } = await api<{ operations: ApiOperation[] }>("/v1/operations?limit=100");
    return operations.filter((op) => op.status === "awaiting_approval" || (op.approval_actions?.length ?? 0) > 0).map(approvalFromOperation);
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
    const r = await optional("pending approvals", api<{ operations: ApiOperation[] }>("/v1/operations?status=awaiting_approval&limit=200"));
    return r ? r.operations.length : null;
  },

  async listTokens() {
    const { tokens } = await api<{ tokens: ApiTokenRow[] }>("/v1/tokens");
    return tokens.map(tokenFromApi);
  },
  async createToken(input) {
    // Not retried: a repeated POST would mint a second token. The secret is returned once.
    const r = await api<{ token: ApiTokenRow; secret: string }>("/v1/tokens", { method: "POST", body: input });
    return { token: tokenFromApi(r.token), secret: r.secret };
  },
  async revokeToken(id) {
    await api<void>(`/v1/tokens/${enc(id)}`, { method: "DELETE" });
  },
  async listProviders() {
    const { providers } = await api<{ providers: ApiProvider[] }>("/v1/ai/providers");
    return providers.map(providerFromApi);
  },
  async saveProviderKey(id, input) {
    const p = await api<ApiProvider>(`/v1/ai/providers/${enc(id)}`, {
      method: "PUT",
      body: { key: input.key || undefined, base_url: input.baseUrl || undefined, model: input.model || undefined },
    });
    return providerFromApi(p);
  },
  async removeProviderKey(id) {
    await api<void>(`/v1/ai/providers/${enc(id)}`, { method: "DELETE" });
  },
  async getBilling() {
    return billingFromApi(await api<ApiBilling>("/v1/billing"));
  },
  async getGcpLink() {
    // New apps default to ShipCube's project; the console shows the first active link, as billing does.
    const t = await api<{ deployer_sa: string; targets: ApiGcpLink[] }>("/v1/gcp-targets");
    const active = t.targets.find((l) => l.status === "active");
    return { deployer: t.deployer_sa, link: active ? linkFromApi(active) : { linked: false } };
  },
  async linkGcpProject(input) {
    // Safe to repeat: the API updates the existing link for the same project.
    return linkFromApi(
      await api<ApiGcpLink>("/v1/gcp-targets", { method: "POST", body: { project_id: input.projectId, region: input.region } }),
    );
  },
  async getWarehouse() {
    return warehouseFromApi(await api<unknown>("/v1/data/warehouse"));
  },
  async connectWarehouse(input, save) {
    // The API tests the credentials first and never echoes them; PUT also stores them in Secret Manager.
    if (!save) return api<{ tables: number; schemas: number }>("/v1/data/warehouse:test", { method: "POST", body: warehouseBody(input) });
    const out = await api<{ tables: number; schemas: number; warehouse: unknown }>("/v1/data/warehouse", {
      method: "PUT",
      body: warehouseBody(input),
    });
    return { ...out, warehouse: warehouseFromApi(out.warehouse) };
  },
  async listCubes() {
    return camelize<Cube[]>(await api<unknown>("/v1/data/cubes"));
  },
  async getCube(name) {
    const c = await orNull(api<unknown>(`/v1/data/cubes/${enc(name)}`));
    return c === null ? null : camelize<Cube>(c);
  },
  async listMigrations() {
    return camelize<Migration[]>(await api<unknown>("/v1/data/migrations"));
  },
  async getMigration(id) {
    const m = await orNull(api<unknown>(`/v1/data/migrations/${enc(id)}`));
    return m === null ? null : camelize<Migration>(m);
  },
  async decideMigration(id, decision, { allowBreaking, note }) {
    // A person's console session only; the API refuses PATs. Deciding twice answers 409.
    const m = await api<unknown>(`/v1/data/migrations/${enc(id)}:${decision}`, {
      method: "POST",
      body: decision === "apply" ? { allow_breaking: allowBreaking } : { note },
      idempotencyKey: `${decision}-${id}`,
    });
    return camelize<Migration>(m);
  },

  async requestAccessSignedIn({ name, useCase, termsVersion }) {
    // The API takes the email from the verified session; anonymous requests come from the browser.
    await api<unknown>("/v1/waitlist", { method: "POST", body: { name, use_case: useCase, terms_version: termsVersion } });
  },
  async accountStatus() {
    return accountStatusFrom(
      () => api<{ platform_admin?: boolean }>("/v1/me"),
      () => api<ApiWaitlistMe>("/v1/waitlist/me"),
    );
  },
  async listWaitlist(status): Promise<WaitlistPage> {
    const r = await api<{ requests: ApiWaitlistRequest[]; workspaces?: { count: number; cap: number } }>("/v1/admin/waitlist", {
      query: { status, limit: 500 },
    });
    return { requests: r.requests.map(waitlistFromApi), workspaces: r.workspaces };
  },
  async decideWaitlist(id, decision) {
    // Approving creates the workspace. Repeating a decision returns it again; the opposite one is
    // 409 conflict, and approving past the workspace cap is 409 workspace_cap_reached. Both show the API's message.
    return waitlistFromApi(await api<ApiWaitlistRequest>(`/v1/admin/waitlist/${enc(id)}/${decision}`, { method: "POST" }));
  },
};
