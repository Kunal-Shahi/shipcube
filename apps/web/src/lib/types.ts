// Types shared by the console. They mirror what the control-plane API is expected to
// return (services/api). Until that API exists, src/lib/api.ts serves mock data.

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
export type Health = "healthy" | "degraded" | "down" | "deploying";
export type Freshness = "fresh" | "stale" | "unknown";
export type Billing = "shipcube" | "own-gcp";

export interface AppSummary {
  id: string;
  name: string;
  kind: AppKind;
  tier: TierId;
  health: Health;
  url: string;
  region: string;
  billing: Billing;
  costMonthToDate: number;
  spendCap: number;
  requests24h: number;
  sharedWith: number;
  lastDeployAt: string;
  deployedBy: string;
  aiProvider: string;
}

export interface LoadEstimate {
  workloadClass: WorkloadClass;
  confidence: number;
  recommendedTier: TierId;
  cpu: string;
  memory: string;
  minInstances: number;
  maxInstances: number;
  monthlyCost: number;
  reasoning: string[];
  signals: { label: string; value: string }[];
  estimatedBy: string;
  estimatedAt: string;
  overridden: boolean;
}

export interface Caps {
  monthlySpend: number;
  requestsPerMinute: number;
  concurrency: number;
  maxInstances: number;
  memory: string;
  llmTokensPerDay: number;
}

export type GrantKind = "user" | "group" | "domain";
export type Role = "owner" | "editor" | "viewer";

export interface AccessGrant {
  id: string;
  kind: GrantKind;
  principal: string;
  role: Role;
  addedBy: string;
  addedAt: string;
}

export interface SeriesPoint {
  t: string;
  value: number;
}

export interface Monitoring {
  health: Health;
  uptime30d: number;
  p50Ms: number;
  p95Ms: number;
  errorRate: number;
  coldStarts24h: number;
  freshness: Freshness;
  sourceUpdatedAt: string;
  freshnessSlaMinutes: number;
  costMonthToDate: number;
  costBreakdown: { label: string; value: number }[];
  driftScore: number;
  driftNote: string;
  latency: { t: string; p50: number; p95: number }[];
  dailyCost: SeriesPoint[];
  replacement: null | {
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
  source: "build" | "deploy" | "runtime" | "gateway";
  message: string;
}

export interface AppDetail extends AppSummary {
  description: string;
  repo: string;
  createdAt: string;
  estimate: LoadEstimate;
  caps: Caps;
  usage: { spend: number; peakRpm: number; peakConcurrency: number; tokensToday: number };
  access: AccessGrant[];
  monitoring: Monitoring;
  logs: LogLine[];
}

export type ProviderId = "meta" | "openai-compatible" | "openai" | "anthropic";

export interface ProviderConfig {
  id: ProviderId;
  label: string;
  description: string;
  connected: boolean;
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
  lastUsedAt: string | null;
}

export interface GcpLink {
  linked: boolean;
  projectId?: string;
  billingAccount?: string;
  region?: string;
  linkedAt?: string;
  serviceAccount?: string;
}

export interface BillingOverview {
  credits: { granted: number; used: number; expiresAt: string };
  gcp: GcpLink;
  monthToDate: number;
  forecast: number;
  byApp: { appId: string; name: string; billing: Billing; compute: number; data: number; llm: number }[];
  daily: SeriesPoint[];
}
