// Typed mock data for the console. Everything here is invented for the demo:
// the apps are built on the public Olist e-commerce dataset used in docs/PLAN.md.

import type {
  AccessGrant,
  ApiToken,
  AppDetail,
  BillingOverview,
  LogLine,
  Monitoring,
  ProviderConfig,
  SeriesPoint,
} from "./types";

// A fixed clock so relative times ("14 min ago") are stable across server and client.
export const MOCK_NOW = new Date("2026-10-27T18:00:00Z").getTime();

function seeded(seed: number) {
  let s = seed;
  return () => {
    s = (s * 16807) % 2147483647;
    return (s - 1) / 2147483646;
  };
}

function hoursAgo(h: number) {
  return new Date(MOCK_NOW - h * 3600_000).toISOString();
}
function minutesAgo(m: number) {
  return new Date(MOCK_NOW - m * 60_000).toISOString();
}

function latencySeries(seed: number, base: number, spread: number, busyFactor = 1) {
  const rnd = seeded(seed);
  return Array.from({ length: 24 }, (_, i) => {
    const hour = (new Date(MOCK_NOW).getUTCHours() - 23 + i + 24) % 24;
    const workHours = hour >= 13 && hour <= 23 ? busyFactor : 1;
    const p50 = Math.round(base * workHours * (0.85 + rnd() * 0.3));
    const p95 = Math.round(p50 * (2.1 + rnd() * spread));
    return { t: hoursAgo(23 - i), p50, p95 };
  });
}

function dailySeries(seed: number, base: number, days = 27): SeriesPoint[] {
  const rnd = seeded(seed);
  return Array.from({ length: days }, (_, i) => {
    const d = new Date(Date.UTC(2026, 9, i + 1));
    const weekend = d.getUTCDay() === 0 || d.getUTCDay() === 6;
    return { t: d.toISOString(), value: +(base * (weekend ? 0.45 : 1) * (0.8 + rnd() * 0.4)).toFixed(2) };
  });
}

const owner: AccessGrant = {
  id: "g_owner",
  kind: "user",
  principal: "kunal@acme-analytics.com",
  role: "owner",
  addedBy: "ShipCube",
  addedAt: "2026-10-14T09:12:00Z",
};

function logs(appSlug: string, route: string, extra: Omit<LogLine, "id">[] = []): LogLine[] {
  const [r1, r2] = [route.replace("{v}", "SP"), route.replace("{v}", "RJ")];
  const base: Omit<LogLine, "id">[] = [
    { ts: hoursAgo(26.2), level: "info", source: "build", message: `Building ${appSlug} from commit 8f3c2d1 (Dockerfile detected)` },
    { ts: hoursAgo(26.1), level: "info", source: "build", message: "Image pushed to us-central1-docker.pkg.dev/shipcube-apps/apps/" + appSlug + ":8f3c2d1 (142 MB)" },
    { ts: hoursAgo(25.9), level: "info", source: "deploy", message: "Revision " + appSlug + "-00007-kq2 serving 100% of traffic" },
    { ts: hoursAgo(3.2), level: "info", source: "runtime", message: `GET ${r1} 200 184ms` },
    { ts: hoursAgo(3.1), level: "info", source: "gateway", message: "Access granted: maria@acme-analytics.com (group finance-team@)" },
    { ts: hoursAgo(2.7), level: "warn", source: "runtime", message: "Cold start 2.4s (instance scaled from 0)" },
    { ts: hoursAgo(1.4), level: "info", source: "runtime", message: "Semantic query L0 compiled in 12ms, cache hit" },
    { ts: minutesAgo(22), level: "info", source: "runtime", message: `GET ${r2} 200 161ms` },
    { ts: minutesAgo(9), level: "warn", source: "gateway", message: "Access denied: alex@gmail.com is not on the access list" },
    { ts: minutesAgo(3), level: "info", source: "runtime", message: "GET / 200 48ms" },
    ...extra,
  ];
  return base
    .sort((a, b) => a.ts.localeCompare(b.ts))
    .map((l, i) => ({ ...l, id: `${appSlug}-${i}` }));
}

function monitoring(partial: Partial<Monitoring> & Pick<Monitoring, "latency" | "dailyCost">): Monitoring {
  const p50s = partial.latency.map((p) => p.p50).sort((a, b) => a - b);
  const p95s = partial.latency.map((p) => p.p95).sort((a, b) => a - b);
  const mtd = +partial.dailyCost.reduce((s, d) => s + d.value, 0).toFixed(2);
  return {
    health: "healthy",
    uptime30d: 99.97,
    p50Ms: p50s[12],
    p95Ms: p95s[22],
    errorRate: 0.08,
    coldStarts24h: 6,
    freshness: "fresh",
    sourceUpdatedAt: minutesAgo(14),
    freshnessSlaMinutes: 60,
    costMonthToDate: mtd,
    costBreakdown: [
      { label: "Compute", value: +(mtd * 0.62).toFixed(2) },
      { label: "Warehouse queries", value: +(mtd * 0.27).toFixed(2) },
      { label: "LLM tokens", value: +(mtd * 0.11).toFixed(2) },
    ],
    driftScore: 0.04,
    driftNote: "Output distributions match the previous 7 runs.",
    replacement: null,
    ...partial,
  };
}

export const apps: AppDetail[] = [
  {
    id: "app_rrt01",
    name: "regional-revenue-tracker",
    kind: "tracker",
    description: "Monthly revenue and share of orders by Brazilian state, with month-over-month change in share.",
    repo: "github.com/acme-analytics/olist-trackers",
    tier: "T1",
    health: "healthy",
    url: "https://regional-revenue-tracker.apps.shipcube.dev",
    region: "us-central1",
    billing: "own-gcp",
    costMonthToDate: 0,
    spendCap: 25,
    requests24h: 4_812,
    sharedWith: 14,
    lastDeployAt: hoursAgo(26),
    deployedBy: "Claude Code",
    createdAt: "2026-10-14T09:12:00Z",
    aiProvider: "Muse (default)",
    estimate: {
      workloadClass: "bursty-interactive",
      confidence: 0.91,
      recommendedTier: "T1",
      cpu: "1 vCPU",
      memory: "512 MiB",
      minInstances: 0,
      maxInstances: 4,
      monthlyCost: 6.8,
      reasoning: [
        "Requests arrive in short bursts during São Paulo business hours and the app is idle for about 14 hours a day, so scale-to-zero Cloud Run avoids paying for idle time.",
        "Peak memory in the sandbox run was 212 MiB on the full Olist sample. 512 MiB leaves headroom for the largest state (SP, 41% of orders).",
        "Each page load runs 3 L1 semantic queries that hit the result cache after the first request, so CPU stays low after warm-up.",
        "A cold start adds about 2.4 s. At the observed traffic that affects roughly 1 in 80 requests, which is acceptable for an internal tracker.",
      ],
      signals: [
        { label: "Peak RSS", value: "212 MiB" },
        { label: "CPU p95", value: "0.31 vCPU" },
        { label: "Wall time / request", value: "180 ms" },
        { label: "Queries / request", value: "3" },
        { label: "Burstiness (CV)", value: "2.8" },
        { label: "Idle share", value: "58%" },
      ],
      estimatedBy: "workload classifier v0.3",
      explainedBy: "Muse Spark",
      estimatedAt: "2026-10-14T09:10:00Z",
      overridden: false,
    },
    caps: { monthlySpend: 25, requestsPerMinute: 600, concurrency: 80, maxInstances: 4, memory: "512 MiB", llmTokensPerDay: 200_000 },
    usage: { spend: 0, peakRpm: 142, peakConcurrency: 11, tokensToday: 38_410 },
    access: [
      owner,
      { id: "g2", kind: "group", principal: "finance-team@acme-analytics.com", role: "viewer", addedBy: "kunal@acme-analytics.com", addedAt: "2026-10-15T11:02:00Z" },
      { id: "g3", kind: "user", principal: "maria.souza@acme-analytics.com", role: "editor", addedBy: "kunal@acme-analytics.com", addedAt: "2026-10-16T08:40:00Z" },
      { id: "g4", kind: "domain", principal: "olist-partners.com.br", role: "viewer", addedBy: "kunal@acme-analytics.com", addedAt: "2026-10-21T15:20:00Z" },
    ],
    monitoring: monitoring({ latency: latencySeries(7, 70, 0.8, 1.6), dailyCost: dailySeries(3, 0.21) }),
    logs: logs("regional-revenue-tracker", "/api/revenue?region={v}", [
      { ts: hoursAgo(26.0), level: "info", source: "deploy", message: "Placement: T1 Serverless, 1 vCPU / 512 MiB, max 4 instances" },
      { ts: hoursAgo(0.9), level: "info", source: "gateway", message: "AI gateway: muse-spark 1,204 tokens (summary panel)" },
    ]),
  },
  {
    id: "app_ssc02",
    name: "seller-scorecard",
    kind: "dashboard",
    description: "Seller ratings, late-delivery rate and cancellation rate for every Olist seller, refreshed hourly.",
    repo: "github.com/acme-analytics/seller-scorecard",
    tier: "T1",
    health: "degraded",
    url: "https://seller-scorecard.apps.shipcube.dev",
    region: "us-central1",
    billing: "shipcube",
    costMonthToDate: 0,
    spendCap: 40,
    requests24h: 31_604,
    sharedWith: 42,
    lastDeployAt: hoursAgo(74),
    deployedBy: "Cursor",
    createdAt: "2026-10-09T13:30:00Z",
    aiProvider: "Muse (default)",
    estimate: {
      workloadClass: "bursty-interactive",
      confidence: 0.74,
      recommendedTier: "T1",
      cpu: "1 vCPU",
      memory: "1 GiB",
      minInstances: 0,
      maxInstances: 8,
      monthlyCost: 14.2,
      reasoning: [
        "At deploy time traffic came from one team in short bursts, so scale-to-zero Cloud Run was the cheapest fit.",
        "The seller table has 3,095 rows and renders server-side; 1 GiB covers the 640 MiB peak measured in the sandbox.",
        "Confidence was lower than usual (0.74) because the sandbox run used synthetic traffic. ShipCube is watching for a re-placement.",
      ],
      signals: [
        { label: "Peak RSS", value: "640 MiB" },
        { label: "CPU p95", value: "0.62 vCPU" },
        { label: "Wall time / request", value: "410 ms" },
        { label: "Queries / request", value: "5" },
        { label: "Burstiness (CV)", value: "1.9" },
        { label: "Idle share", value: "31%" },
      ],
      estimatedBy: "workload classifier v0.3",
      explainedBy: "Muse Spark",
      estimatedAt: "2026-10-09T13:28:00Z",
      overridden: false,
    },
    caps: { monthlySpend: 40, requestsPerMinute: 1200, concurrency: 80, maxInstances: 8, memory: "1 GiB", llmTokensPerDay: 100_000 },
    usage: { spend: 0, peakRpm: 1_034, peakConcurrency: 71, tokensToday: 12_220 },
    access: [
      owner,
      { id: "g5", kind: "domain", principal: "acme-analytics.com", role: "viewer", addedBy: "kunal@acme-analytics.com", addedAt: "2026-10-10T10:00:00Z" },
      { id: "g6", kind: "group", principal: "seller-ops@acme-analytics.com", role: "editor", addedBy: "kunal@acme-analytics.com", addedAt: "2026-10-10T10:05:00Z" },
    ],
    monitoring: monitoring({
      health: "degraded",
      latency: latencySeries(11, 160, 2.2, 2.4),
      dailyCost: dailySeries(5, 1.1),
      errorRate: 1.9,
      coldStarts24h: 41,
      uptime30d: 99.62,
      driftScore: 0.11,
      driftNote: "Late-delivery rate for sellers in RJ shifted from 8% to 13% since Oct 20.",
      replacement: {
        fromTier: "T1",
        toTier: "T3",
        reason:
          "Traffic is now steady from 13:00 to 23:00 UTC (idle share fell from 31% to 6%), and 41 cold starts in 24 h push p95 past 1 s. One always-warm instance removes cold starts and costs less than the current burst scaling.",
        currentMonthly: 31.4,
        proposedMonthly: 22.9,
      },
    }),
    logs: logs("seller-scorecard", "/sellers?state={v}", [
      { ts: minutesAgo(41), level: "error", source: "runtime", message: "GET /sellers?state=RJ 503 upstream request timeout after 10000ms" },
      { ts: minutesAgo(40), level: "warn", source: "runtime", message: "Concurrency 71/80 (89% of cap)" },
      { ts: minutesAgo(12), level: "warn", source: "runtime", message: "Cold start 3.1s (instance scaled from 0)" },
    ]),
  },
  {
    id: "app_wor03",
    name: "weekly-ops-report",
    kind: "report",
    description: "Monday operations report: orders, freight cost and delivery time by carrier, emailed as a link.",
    repo: "github.com/acme-analytics/ops-reports",
    tier: "T0",
    health: "healthy",
    url: "https://weekly-ops-report.apps.shipcube.dev",
    region: "us-central1",
    billing: "shipcube",
    costMonthToDate: 0,
    spendCap: 5,
    requests24h: 96,
    sharedWith: 8,
    lastDeployAt: hoursAgo(152),
    deployedBy: "Muse Code",
    createdAt: "2026-10-02T07:00:00Z",
    aiProvider: "Muse (default)",
    estimate: {
      workloadClass: "batch",
      confidence: 0.96,
      recommendedTier: "T0",
      cpu: "1 vCPU (refresh job)",
      memory: "1 GiB (refresh job)",
      minInstances: 0,
      maxInstances: 1,
      monthlyCost: 0.4,
      reasoning: [
        "The report changes once a week, so it is precomputed on Monday at 06:00 BRT and served as a static page.",
        "Readers never trigger a query, so serving cost is close to zero.",
      ],
      signals: [
        { label: "Peak RSS", value: "780 MiB" },
        { label: "Refresh wall time", value: "2 m 14 s" },
        { label: "Queries / refresh", value: "11" },
        { label: "Output size", value: "184 KB" },
      ],
      estimatedBy: "workload classifier v0.3",
      explainedBy: "Muse Spark",
      estimatedAt: "2026-10-02T06:58:00Z",
      overridden: false,
    },
    caps: { monthlySpend: 5, requestsPerMinute: 120, concurrency: 20, maxInstances: 1, memory: "1 GiB", llmTokensPerDay: 20_000 },
    usage: { spend: 0, peakRpm: 9, peakConcurrency: 2, tokensToday: 0 },
    access: [owner, { id: "g7", kind: "group", principal: "ops-leads@acme-analytics.com", role: "viewer", addedBy: "kunal@acme-analytics.com", addedAt: "2026-10-02T07:05:00Z" }],
    monitoring: monitoring({
      latency: latencySeries(19, 22, 0.5),
      dailyCost: dailySeries(9, 0.015),
      freshness: "stale",
      sourceUpdatedAt: hoursAgo(31),
      freshnessSlaMinutes: 24 * 60,
      coldStarts24h: 0,
    }),
    logs: logs("weekly-ops-report", "/?week=2026-W43&carrier={v}"),
  },
  {
    id: "app_csj04",
    name: "churn-scoring-job",
    kind: "job",
    description: "Nightly job that scores every customer for churn risk and writes results to the warehouse.",
    repo: "github.com/acme-analytics/churn-model",
    tier: "T2",
    health: "healthy",
    url: "https://console.shipcube.dev/apps/app_csj04",
    region: "us-central1",
    billing: "own-gcp",
    costMonthToDate: 0,
    spendCap: 30,
    requests24h: 1,
    sharedWith: 3,
    lastDeployAt: hoursAgo(8),
    deployedBy: "Claude Code",
    createdAt: "2026-10-18T19:00:00Z",
    aiProvider: "Fireworks (BYOK)",
    estimate: {
      workloadClass: "memory-heavy",
      confidence: 0.88,
      recommendedTier: "T2",
      cpu: "2 vCPU",
      memory: "4 GiB",
      minInstances: 0,
      maxInstances: 1,
      monthlyCost: 9.1,
      reasoning: [
        "Runs once a night for about 6 minutes and holds the full 96k-customer feature matrix in memory (peak 2.9 GiB).",
        "A Cloud Run job with 4 GiB fits; nothing needs to stay warm between runs.",
      ],
      signals: [
        { label: "Peak RSS", value: "2.9 GiB" },
        { label: "Wall time", value: "6 m 02 s" },
        { label: "Rows written", value: "96,096" },
        { label: "Schedule", value: "Daily 03:00 BRT" },
      ],
      estimatedBy: "workload classifier v0.3",
      explainedBy: "Muse Spark",
      estimatedAt: "2026-10-18T18:57:00Z",
      overridden: false,
    },
    caps: { monthlySpend: 30, requestsPerMinute: 10, concurrency: 1, maxInstances: 1, memory: "4 GiB", llmTokensPerDay: 500_000 },
    usage: { spend: 0, peakRpm: 1, peakConcurrency: 1, tokensToday: 412_900 },
    access: [owner],
    monitoring: monitoring({
      latency: latencySeries(23, 340, 0.3),
      dailyCost: dailySeries(13, 0.34),
      coldStarts24h: 1,
      driftScore: 0.07,
      driftNote: "Share of customers scored high-risk rose from 11.2% to 12.0% over 7 runs. Within the expected range.",
    }),
    logs: logs("churn-scoring-job", "/run?segment={v}"),
  },
  {
    id: "app_dsa05",
    name: "delivery-sla-api",
    kind: "api",
    description: "Internal API that returns the promised and predicted delivery date for an order.",
    repo: "github.com/acme-analytics/delivery-sla",
    tier: "T3",
    health: "deploying",
    url: "https://delivery-sla-api.apps.shipcube.dev",
    region: "us-central1",
    billing: "shipcube",
    costMonthToDate: 0,
    spendCap: 60,
    requests24h: 0,
    sharedWith: 2,
    lastDeployAt: minutesAgo(2),
    deployedBy: "Claude Code",
    createdAt: minutesAgo(6),
    aiProvider: "None",
    estimate: {
      workloadClass: "steady-service",
      confidence: 0.83,
      recommendedTier: "T3",
      cpu: "1 vCPU",
      memory: "512 MiB",
      minInstances: 1,
      maxInstances: 6,
      monthlyCost: 38.5,
      reasoning: [
        "Other services call this API on every checkout, so traffic is continuous and latency matters more than idle cost.",
        "One warm instance keeps p95 under 100 ms; autoscaling to 6 covers the Black Friday peak in last year's traces.",
      ],
      signals: [
        { label: "Peak RSS", value: "180 MiB" },
        { label: "CPU p95", value: "0.4 vCPU" },
        { label: "Expected RPS", value: "12–40" },
        { label: "Idle share", value: "0%" },
      ],
      estimatedBy: "workload classifier v0.3",
      explainedBy: "Muse Spark",
      estimatedAt: minutesAgo(5),
      overridden: false,
    },
    caps: { monthlySpend: 60, requestsPerMinute: 3000, concurrency: 200, maxInstances: 6, memory: "512 MiB", llmTokensPerDay: 0 },
    usage: { spend: 0, peakRpm: 0, peakConcurrency: 0, tokensToday: 0 },
    access: [owner, { id: "g8", kind: "group", principal: "checkout-service@acme-analytics.com", role: "viewer", addedBy: "kunal@acme-analytics.com", addedAt: minutesAgo(5) }],
    monitoring: monitoring({ health: "deploying", latency: [], dailyCost: [], freshness: "unknown", coldStarts24h: 0, uptime30d: 0, errorRate: 0 }),
    logs: [
      { id: "d1", ts: minutesAgo(3), level: "info", source: "build", message: "Building delivery-sla-api from commit a41e9b0 (buildpacks: python 3.12)" },
      { id: "d2", ts: minutesAgo(2), level: "info", source: "build", message: "Image pushed (96 MB)" },
      { id: "d3", ts: minutesAgo(1), level: "info", source: "deploy", message: "Placement: T3 Always-on API, 1 vCPU / 512 MiB, min 1, max 6" },
      { id: "d4", ts: minutesAgo(0.5), level: "info", source: "deploy", message: "Waiting for revision delivery-sla-api-00001-x7d to become ready" },
    ],
  },
  {
    id: "app_stt06",
    name: "support-ticket-triage",
    kind: "tracker",
    description: "Classifies new support tickets by topic and urgency and tracks backlog by team.",
    repo: "github.com/acme-analytics/support-triage",
    tier: "T1",
    health: "down",
    url: "https://support-ticket-triage.apps.shipcube.dev",
    region: "us-central1",
    billing: "shipcube",
    costMonthToDate: 0,
    spendCap: 15,
    requests24h: 212,
    sharedWith: 6,
    lastDeployAt: hoursAgo(5),
    deployedBy: "Muse Code",
    createdAt: "2026-10-20T16:45:00Z",
    aiProvider: "Anthropic (BYOK)",
    estimate: {
      workloadClass: "io-bound",
      confidence: 0.79,
      recommendedTier: "T1",
      cpu: "1 vCPU",
      memory: "512 MiB",
      minInstances: 0,
      maxInstances: 3,
      monthlyCost: 4.6,
      reasoning: [
        "Most time is spent waiting on the model provider, not on CPU, so a small instance with high concurrency fits.",
        "Tickets arrive a few times an hour; scale-to-zero keeps the cost under $5/month.",
      ],
      signals: [
        { label: "Peak RSS", value: "164 MiB" },
        { label: "CPU p95", value: "0.08 vCPU" },
        { label: "Time waiting on I/O", value: "92%" },
      ],
      estimatedBy: "workload classifier v0.3",
      explainedBy: "Muse Spark",
      estimatedAt: "2026-10-20T16:43:00Z",
      overridden: false,
    },
    caps: { monthlySpend: 15, requestsPerMinute: 60, concurrency: 40, maxInstances: 3, memory: "512 MiB", llmTokensPerDay: 150_000 },
    usage: { spend: 0, peakRpm: 14, peakConcurrency: 3, tokensToday: 150_000 },
    access: [owner, { id: "g9", kind: "group", principal: "support@acme-analytics.com", role: "editor", addedBy: "kunal@acme-analytics.com", addedAt: "2026-10-20T17:00:00Z" }],
    monitoring: monitoring({
      health: "down",
      latency: latencySeries(31, 900, 0.6),
      dailyCost: dailySeries(17, 0.12, 27),
      errorRate: 100,
      uptime30d: 98.1,
      freshness: "stale",
      sourceUpdatedAt: hoursAgo(5),
      driftScore: 0,
      driftNote: "No successful runs in the last 5 hours.",
    }),
    logs: logs("support-ticket-triage", "/tickets?team={v}", [
      { ts: hoursAgo(5.1), level: "error", source: "gateway", message: "LLM token cap reached: 150,000 of 150,000 tokens today. Requests to the AI gateway are blocked until 00:00 UTC." },
      { ts: hoursAgo(4.9), level: "error", source: "runtime", message: "POST /classify 429 ai_gateway: daily token cap reached" },
      { ts: minutesAgo(30), level: "error", source: "runtime", message: "POST /classify 429 ai_gateway: daily token cap reached" },
    ]),
  },
];

// A soft-deleted app, restorable until 7 days after deletedAt.
apps.push({
  ...apps[2],
  id: "app_q3p07",
  name: "q3-promo-tracker",
  kind: "tracker",
  tier: "T0",
  description: "Tracked orders and revenue from the Q3 free-shipping promotion by state.",
  repo: "github.com/acme-analytics/olist-trackers",
  health: "healthy",
  url: "https://q3-promo-tracker.apps.shipcube.dev",
  requests24h: 0,
  sharedWith: 5,
  lastDeployAt: "2026-09-02T14:00:00Z",
  deployedBy: "Claude Code",
  createdAt: "2026-07-01T10:00:00Z",
  deletedAt: hoursAgo(50),
  access: [owner],
  logs: [
    { id: "q1", ts: hoursAgo(50.1), level: "info", source: "deploy", message: "delete_app requested by Claude Code; approved by kunal@acme-analytics.com" },
    { id: "q2", ts: hoursAgo(50), level: "info", source: "deploy", message: "Traffic stopped. Service scaled to zero and kept for 7 days" },
  ],
  monitoring: { ...apps[2].monitoring, latency: [], dailyCost: [], replacement: null },
});

for (const app of apps) {
  app.costMonthToDate = app.monitoring.costMonthToDate;
  app.usage.spend = app.monitoring.costMonthToDate;
}

export const providers: ProviderConfig[] = [
  {
    id: "meta",
    label: "Meta Muse",
    description: "Muse Spark through the Meta Model API. Default for every app; ShipCube's own load estimation always uses it.",
    connected: true,
    defaultModel: "muse-spark",
  },
  {
    id: "openai-compatible",
    label: "OpenAI-compatible",
    description: "Any endpoint that speaks the OpenAI chat API: Fireworks, Together, Baseten, OpenRouter, or your own vLLM or Ollama.",
    connected: true,
    keyHint: "fw_…9Q2c",
    baseUrl: "https://api.fireworks.ai/inference/v1",
    defaultModel: "accounts/fireworks/models/llama-v3p3-70b-instruct",
    addedAt: "2026-10-18T19:04:00Z",
  },
  {
    id: "openai",
    label: "OpenAI",
    description: "Your OpenAI API key.",
    connected: false,
  },
  {
    id: "anthropic",
    label: "Anthropic",
    description: "Your Anthropic API key. Claude subscriptions (claude.ai sign-in) can't be used by third-party products.",
    connected: true,
    keyHint: "sk-ant-…h4Tw",
    defaultModel: "claude-haiku-5-5",
    addedAt: "2026-10-20T16:50:00Z",
  },
];

export const tokens: ApiToken[] = [
  { id: "tok_1", name: "MacBook – Claude Code", prefix: "sc_pat_7Hq2xK9m", scopes: ["apps:read", "apps:deploy", "caps:write", "access:write", "data:read", "data:migrate"], createdAt: "2026-10-14T09:00:00Z", expiresAt: "2026-11-13T09:00:00Z", lastUsedAt: minutesAgo(6) },
  { id: "tok_2", name: "Cursor", prefix: "sc_pat_Lm9xQ2vT", scopes: ["apps:read", "apps:deploy"], createdAt: "2026-10-09T13:20:00Z", expiresAt: "2026-11-08T13:20:00Z", lastUsedAt: hoursAgo(74) },
];

export const billing: BillingOverview = {
  credits: { granted: 300, used: 0, expiresAt: "2027-01-24T00:00:00Z" },
  gcp: {
    linked: true,
    projectId: "acme-analytics-prod",
    billingAccount: "01A2B3-C4D5E6-F7G8H9",
    region: "us-central1",
    linkedAt: "2026-10-14T08:55:00Z",
    serviceAccount: "shipcube-deployer@acme-analytics-prod.iam.gserviceaccount.com",
  },
  monthToDate: 0,
  forecast: 0,
  byApp: apps.map((a) => {
    const [compute, data, llm] = a.monitoring.costBreakdown.map((c) => c.value);
    return { appId: a.id, name: a.name, billing: a.billing, compute: compute ?? 0, data: data ?? 0, llm: llm ?? 0 };
  }),
  daily: [],
};

{
  const days = apps[0].monitoring.dailyCost.length;
  billing.daily = Array.from({ length: days }, (_, i) => ({
    t: apps[0].monitoring.dailyCost[i].t,
    value: +apps
      .filter((a) => a.billing === "shipcube")
      .reduce((s, a) => s + (a.monitoring.dailyCost[i]?.value ?? 0), 0)
      .toFixed(2),
  }));
  const shipcubeMtd = billing.daily.reduce((s, d) => s + d.value, 0);
  billing.credits.used = +(shipcubeMtd + 11.84).toFixed(2); // + September usage
  billing.monthToDate = +apps.reduce((s, a) => s + a.costMonthToDate, 0).toFixed(2);
  billing.forecast = +((billing.monthToDate / 27) * 31).toFixed(2);
}
