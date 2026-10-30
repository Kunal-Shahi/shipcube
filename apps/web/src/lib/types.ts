// Types the console renders. The HTTP data source (lib/server/data/http.ts) maps the
// control-plane API (services/api/openapi.yaml) into these; DEV_MODE serves typed mocks.
// Fields the API doesn't return yet are optional, and the UI hides what's missing.

export type TierId = "T0" | "T1" | "T2" | "T3" | "T4";

export const TIERS: Record<TierId, { name: string; target: string }> = {
  T0: { name: "Snapshot", target: "Static page + Cloud Scheduler refresh" },
  T1: { name: "Serverless", target: "Cloud Run, scale to zero" },
  T2: { name: "Scheduled batch", target: "Cloud Run jobs + Cloud Scheduler" },
  T3: { name: "Always-on API", target: "Cloud Run min instances / GKE Autopilot" },
  T4: { name: "Heavy compute", target: "Cloud Run GPU / GKE Autopilot GPU" },
};

export type WorkloadClass =
  | "batch"
  | "bursty-interactive"
  | "steady-service"
  | "memory-heavy"
  | "io-bound"
  | "compute-heavy";

export type AppKind = "tracker" | "dashboard" | "report" | "api" | "job";
export type Health = "healthy" | "degraded" | "down" | "deploying" | "unknown";
export type Freshness = "fresh" | "stale" | "unknown";
export type Billing = "shipcube" | "own-gcp";

export interface AppSummary {
  id: string;
  name: string;
  kind?: AppKind;
  tier: TierId;
  health: Health;
  url: string;
  region?: string;
  billing?: Billing;
  /** Runs in the local stack's Docker, not on GCP (DEV_MODE only). */
  runsLocally?: boolean;
  costMonthToDate?: number;
  spendCap?: number;
  requests24h?: number;
  sharedWith?: number;
  lastDeployAt: string;
  deployedBy?: string;
  aiProvider?: string;
  /** Soft-deleted apps can be restored for 7 days. */
  deletedAt?: string;
}

export interface LoadEstimate {
  workloadClass: WorkloadClass;
  confidence: number;
  recommendedTier: TierId;
  cpu: string;
  memory: string;
  minInstances: number;
  maxInstances: number;
  /** Expected monthly cost; undefined when the estimate has no usable number. */
  monthlyCost?: number;
  /** The estimator's range around monthlyCost, when it gives one. */
  monthlyCostRange?: { low: number; high: number };
  reasoning: string[];
  signals: { label: string; value: string }[];
  /** The classifier that produced the numbers. */
  estimatedBy: string;
  /** The model that wrote the reasoning; "rules" when Muse was unavailable. */
  explainedBy: string;
  estimatedAt: string;
  overridden: boolean;
}

export interface Caps {
  monthlySpend: number;
  requestsPerMinute: number;
  concurrency: number;
  maxInstances: number;
  memory: string;
  /** Enforced by the AI gateway; not part of the API's Caps yet. */
  llmTokensPerDay?: number;
}

export type GrantKind = "user" | "group" | "domain";
export type Role = "owner" | "editor" | "viewer";

export interface AccessGrant {
  id: string;
  kind: GrantKind;
  principal: string;
  role: Role;
  addedBy?: string;
  addedAt?: string;
}

export interface SeriesPoint {
  t: string;
  value: number;
}

export interface Monitoring {
  /** The window the numbers cover, e.g. "24h". */
  window: string;
  health: Health;
  uptime30d?: number;
  requests?: number;
  p50Ms: number;
  p95Ms: number;
  /** Percent of requests that failed. */
  errorRate: number;
  coldStarts24h: number;
  freshness: Freshness;
  sourceUpdatedAt?: string;
  freshnessSlaMinutes?: number;
  costMonthToDate?: number;
  /** Cost over `costWindow`. */
  costBreakdown: { label: string; value: number }[];
  costWindow: string;
  driftScore?: number;
  driftNote?: string;
  latency: { t: string; p50: number; p95: number }[];
  dailyCost: SeriesPoint[];
  replacement?: null | {
    fromTier: TierId;
    toTier: TierId;
    reason: string;
    currentMonthly: number;
    proposedMonthly: number;
  };
}

export type LogLevel = "info" | "warn" | "error";

export interface LogLine {
  id: string;
  ts: string;
  level: LogLevel;
  source?: "build" | "deploy" | "runtime" | "gateway";
  revision?: string;
  message: string;
}

export interface AppDetail extends AppSummary {
  description?: string;
  repo?: string;
  revision?: string;
  createdAt?: string;
  estimate?: LoadEstimate;
  caps: Caps;
  usage?: { spend?: number; peakRpm?: number; peakConcurrency?: number; tokensToday?: number };
  access: AccessGrant[];
  monitoring?: Monitoring;
  logs: LogLine[];
  restorableUntil?: string;
}

export type ProviderId = "meta" | "openai-compatible" | "openai" | "anthropic";

export interface ProviderConfig {
  id: ProviderId;
  label: string;
  description: string;
  connected: boolean;
  /** The workspace's default provider: apps without an override use it. Saving a key makes that provider the default. */
  isDefault?: boolean;
  keyHint?: string;
  baseUrl?: string;
  defaultModel?: string;
  addedAt?: string;
}

export interface ApiToken {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  createdAt: string;
  expiresAt: string;
  lastUsedAt: string | null;
}

export interface GcpLink {
  linked: boolean;
  projectId?: string;
  billingAccount?: string;
  region?: string;
  linkedAt?: string;
  serviceAccount?: string;
  /** broken: the deployer still lacks some of these permissions (link-project.sh hasn't run, or was undone). */
  status?: "active" | "broken";
  missingPermissions?: string[];
}

export interface BillingOverview {
  /** granted is 0 when the deployment has no credit grant configured; expiresAt may be unknown. */
  credits: { granted: number; used: number; expiresAt?: string };
  gcp: GcpLink;
  monthToDate: number;
  forecast: number;
  byApp: { appId: string; name: string; billing: Billing; compute: number; data: number; llm: number }[];
  daily: SeriesPoint[];
}

// Approvals: agent actions that wait for a person in the console (docs/WORKSTREAMS.md, "Decided" §5).
export type ApprovalKind = "first_linked_deploy" | "cap_raise" | "domain_grant" | "delete_app";
export type ApprovalStatus = "pending" | "approved" | "rejected" | "expired";

export interface ImpactRow {
  label: string;
  before?: string;
  after: string;
  tone?: "warning" | "critical";
}

export interface Approval {
  id: string;
  kind: ApprovalKind;
  status: ApprovalStatus;
  title: string;
  appId?: string;
  appName: string;
  requestedBy?: { agent: string; tokenName: string; user: string };
  requestedAt: string;
  expiresAt: string;
  /** What the person asked their agent, when the agent passed it along. */
  prompt?: string;
  /** Why this action needs approval. */
  reason: string;
  /** What happens if it's approved, in order. */
  effects: string[];
  impact: ImpactRow[];
  /** Muse Spark's view, when it had one (for example recommended caps). */
  museNote?: string;
  tool?: { name: string; args: Record<string, unknown> };
  decision?: { by?: string; at: string; note?: string };
}

// Data layer (Kunal-Shahi/shipcube-data): warehouse connection, cubes, migrations.
export type WarehouseKind = "bigquery" | "postgres" | "clickhouse";

export interface Warehouse {
  connected: boolean;
  kind?: WarehouseKind;
  name?: string;
  details?: { label: string; value: string }[];
  connectedAt?: string;
  connectedBy?: string;
  lastCheckedAt?: string;
  modelVersion: number;
}

export interface CubeMember {
  name: string;
  type: "string" | "number" | "time" | "boolean" | "count" | "sum" | "avg" | "count_distinct";
  sql: string;
  description?: string;
  deprecated?: string;
  restrictedTo?: string[];
}

export interface Cube {
  name: string;
  title: string;
  description: string;
  table: string;
  schema: string;
  dimensions: CubeMember[];
  measures: CubeMember[];
  joins: { to: string; relationship: "many_to_one" | "one_to_many" | "one_to_one"; on: string }[];
  segments: { name: string; sql: string }[];
  partition?: string;
  policies: string[];
  usedBy: string[];
}

export type MigrationKind = "additive" | "change" | "breaking";
export type MigrationStatus = "proposed" | "applied" | "rejected";

export interface MigrationImpact {
  breaks: { query: string; app: string; error: string }[];
  changes: { query: string; app: string; note: string }[];
  warnings: { query: string; app: string; warnings: string[] }[];
  unaffected: number;
  appsBroken: string[];
}

export interface Migration {
  id: string;
  description: string;
  status: MigrationStatus;
  kind: MigrationKind;
  proposedBy: { agent: string; tokenName: string; user: string };
  draftedBy?: string;
  proposedAt: string;
  baseVersion: number;
  ops: ({ op: string } & Record<string, unknown>)[];
  diff: { added: string[]; changed: string[]; removed: string[] };
  impact: MigrationImpact;
  applied?: { version: number; at: string; by: string };
  rejected?: { at: string; by: string; note?: string };
}

// Early access: a public waitlist that a platform admin approves by hand. Approval creates the
// person's workspace; their first sign-in with that Google account claims it.
export type WaitlistState = "none" | "pending" | "approved" | "rejected";

/** Who the signed-in person is to ShipCube: a workspace member, or someone on (or off) the waitlist. */
export interface AccountStatus {
  member: boolean;
  /** Can approve waitlist requests. Reported by the API; the API also enforces it. */
  platformAdmin: boolean;
  /** For non-members: where their access request stands. */
  waitlist?: WaitlistState;
  requestedAt?: string;
}

export interface AccessRequestInput {
  email: string;
  name: string;
  useCase: string;
}

export interface WaitlistRequest {
  id: string;
  email: string;
  name: string;
  useCase: string;
  status: Exclude<WaitlistState, "none">;
  createdAt: string;
  decidedAt?: string;
  decidedBy?: string;
}
