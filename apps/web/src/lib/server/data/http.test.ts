import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/errors";
import { accountStatusFrom, approvalFromOperation, billingFromApi, camelize, capsFromApi, capsToApi, estimateFromApi, grantFromApi, healthOf, linkFromApi, placementOf, providerFromApi, summaryFromApi, waitlistFromApi, type ApiOperation } from "./http";

const op = (over: Partial<ApiOperation>): ApiOperation => ({
  id: "op_1",
  kind: "deploy",
  app: "tracker",
  status: "awaiting_approval",
  done: false,
  approval_actions: ["first_linked_deploy"],
  approval_reasons: ["First deploy to a linked project."],
  approval_expires_at: "2026-11-01T00:00:00Z",
  created_at: "2026-10-29T00:00:00Z",
  updated_at: "2026-10-29T00:00:00Z",
  ...over,
});

describe("approvalFromOperation", () => {
  it("maps an awaiting operation to a pending approval", () => {
    const a = approvalFromOperation(op({}));
    expect(a).toMatchObject({ id: "op_1", kind: "first_linked_deploy", status: "pending", appName: "tracker", reason: "First deploy to a linked project." });
    expect(a.decision).toBeUndefined();
  });

  it("maps rejected and expired cancellations", () => {
    expect(approvalFromOperation(op({ status: "cancelled", error: { code: "approval_rejected", message: "no" } })).status).toBe("rejected");
    expect(approvalFromOperation(op({ status: "cancelled", error: { code: "approval_expired", message: "" } })).status).toBe("expired");
  });

  it("treats an operation that moved on as approved", () => {
    expect(approvalFromOperation(op({ status: "running" })).status).toBe("approved");
  });

  it("falls back to the operation kind when approval_actions is missing", () => {
    expect(approvalFromOperation(op({ kind: "delete", approval_actions: [] })).kind).toBe("delete_app");
  });
});

describe("estimateFromApi", () => {
  it("maps the API's estimate, with sizing from the caps deploy set", () => {
    const caps = capsFromApi({ monthly_spend_usd: 20, max_instances: 3, memory: "512Mi", concurrency: 80, requests_per_minute: 120 });
    const e = estimateFromApi(
      { estimate_id: "est_1", workload_class: "bursty-interactive", tier: "T1", confidence: 0.91, monthly_cost_usd: 6.8, explained_by: "muse-spark", created_at: "2026-10-29T10:00:00Z" },
      caps,
      "1",
    );
    expect(e).toMatchObject({
      workloadClass: "bursty-interactive",
      recommendedTier: "T1",
      confidence: 0.91,
      monthlyCost: 6.8,
      cpu: "1 vCPU",
      memory: "512 MiB",
      maxInstances: 3,
      explainedBy: "muse-spark",
      reasoning: [],
      signals: [],
    });
  });
});

describe("estimateFromApi with the estimator's own shapes", () => {
  it("reads a {low, high, expected} cost and an underscored class", () => {
    const caps = capsFromApi({ max_instances: 2, memory: "512Mi" });
    const e = estimateFromApi(
      { estimate_id: "e", workload_class: "bursty_interactive", tier: "T1", confidence: 0.62, monthly_cost_usd: { low: 0.07, high: 0.51, expected: 0.19 }, explained_by: "rules", created_at: "2026-10-29T00:00:00Z" },
      caps,
    );
    expect(e).toMatchObject({ workloadClass: "bursty-interactive", monthlyCost: 0.19, monthlyCostRange: { low: 0.07, high: 0.51 } });
  });
  it("leaves the cost undefined, never NaN, when there's no number", () => {
    const e = estimateFromApi(
      { estimate_id: "e", workload_class: "", tier: "T1", confidence: NaN, monthly_cost_usd: {}, explained_by: "rules", created_at: "2026-10-29T00:00:00Z" },
      capsFromApi({}),
    );
    expect(e.monthlyCost).toBeUndefined();
    expect(e.confidence).toBe(0);
  });
});

describe("placementOf", () => {
  it("shows local apps as local, with no GCP region or billing", () => {
    expect(placementOf({ target: "local", region: "asia-south1" })).toEqual({ runsLocally: true });
    expect(placementOf({ target: "shipcube", region: "asia-south1", runtime: { backend: "local" } })).toEqual({ runsLocally: true });
  });
  it("tells ShipCube's project from a linked one", () => {
    expect(placementOf({ target: "shipcube", region: "asia-south1", runtime: { backend: "gcp" } })).toEqual({ billing: "shipcube", region: "asia-south1" });
    expect(placementOf({ target: "0b6c3a52-61a4-4d2e-9b1f-5c7e8d9a0f12", region: "us-central1" })).toEqual({ billing: "own-gcp", region: "us-central1" });
    expect(placementOf({ target: "0b6c3a52-61a4-4d2e-9b1f-5c7e8d9a0f12", region: "local" })).toEqual({ runsLocally: true });
  });
});

describe("linkFromApi", () => {
  const row = {
    id: "l1",
    project_id: "acme-prod",
    region: "europe-west1",
    deployer_sa: "ws-abc-deployer@shipcube-control-ks.iam.gserviceaccount.com",
    linked_at: "2026-10-29T00:00:00Z",
  };

  it("treats an active link as linked, dated by its last check", () => {
    const l = linkFromApi({ ...row, status: "active", missing_permissions: [], verified_at: "2026-10-30T00:00:00Z" });
    expect(l).toMatchObject({ linked: true, projectId: "acme-prod", region: "europe-west1", linkedAt: "2026-10-30T00:00:00Z", status: "active" });
  });

  it("keeps a broken link unlinked, with what's missing", () => {
    const l = linkFromApi({ ...row, status: "broken", missing_permissions: ["run.services.create"], verified_at: null });
    expect(l).toMatchObject({ linked: false, status: "broken", missingPermissions: ["run.services.create"], linkedAt: "2026-10-29T00:00:00Z" });
  });
});

describe("caps mapping", () => {
  it("round-trips memory between API and UI formats", () => {
    const ui = capsFromApi({ memory: "512Mi", monthly_spend_usd: 25, max_instances: 4, concurrency: 80, requests_per_minute: 600 });
    expect(ui).toEqual({ memory: "512 MiB", monthlySpend: 25, maxInstances: 4, concurrency: 80, requestsPerMinute: 600 });
    expect(capsToApi({ memory: "1 GiB", monthlySpend: 30 })).toMatchObject({ memory: "1Gi", monthly_spend_usd: 30 });
  });
});

describe("grants and health", () => {
  it("splits principal prefixes", () => {
    expect(grantFromApi({ principal: "group:fin@example.com", role: "viewer", granted_at: "t" })).toMatchObject({ kind: "group", principal: "fin@example.com", role: "viewer" });
    expect(grantFromApi({ principal: "domain:example.com", role: "editor", granted_at: "t" })).toMatchObject({ kind: "domain", role: "editor" });
  });

  it("maps unknown statuses to unknown, not healthy", () => {
    expect(healthOf("ready")).toBe("healthy");
    expect(healthOf("FAILED")).toBe("down");
    expect(healthOf("weird")).toBe("unknown");
  });
});

describe("monitoringFromApi", () => {
  it("separates month-to-date cost from the window breakdown and converts the error rate to percent", async () => {
    const { monitoringFromApi } = await import("./http");
    const m = monitoringFromApi(
      {
        app: "a", window: "24h", requests: 412, error_rate: 0.012, latency_ms: { p50: 84, p95: 410.4 }, instances: { avg: 1, max: 2 },
        cold_starts: 6, cpu_utilization: 0.2, memory_utilization: 0.4, cost_usd: { compute: 0.03, ai_tokens: 0.01, month_to_date: 0.62 },
        caps_hit: {}, data_freshness_minutes: 42,
      },
      "healthy",
    );
    expect(m.costMonthToDate).toBe(0.62);
    expect(m.costBreakdown.map((c) => c.label)).toEqual(["Compute", "AI tokens"]);
    expect(m.errorRate).toBe(1.2);
    expect(m.p95Ms).toBe(410);
    expect(m.freshness).toBe("fresh");
  });
});

describe("camelize (/v1/data bodies)", () => {
  it("maps snake_case keys recursively and leaves migration ops untouched", () => {
    const m = camelize<Record<string, unknown>>({
      raw_status: "approved",
      proposed_by: { token_name: "t", user: "u" },
      impact: { apps_broken: ["a"], breaks: [{ query: "q", app: "a", error: "e" }] },
      ops: [{ op: "add_measure", name: "orders.unique_customers", impersonate_service_account: "x" }],
    });
    expect(m).toEqual({
      rawStatus: "approved",
      proposedBy: { tokenName: "t", user: "u" },
      impact: { appsBroken: ["a"], breaks: [{ query: "q", app: "a", error: "e" }] },
      ops: [{ op: "add_measure", name: "orders.unique_customers", impersonate_service_account: "x" }],
    });
  });
});

describe("providerFromApi", () => {
  it("keeps only the key hint, and maps the rest to lib/types", () => {
    const p = providerFromApi({
      id: "anthropic", label: "Anthropic", description: "Your own Anthropic API key.", connected: true, default: true,
      key_hint: "…Wxyz", base_url: null, model: "claude-haiku-5-5", added_at: "2026-10-29T03:00:00Z",
    });
    expect(p).toEqual({
      id: "anthropic", label: "Anthropic", description: "Your own Anthropic API key.", connected: true, isDefault: true,
      keyHint: "…Wxyz", baseUrl: undefined, defaultModel: "claude-haiku-5-5", addedAt: "2026-10-29T03:00:00Z",
    });
  });
});

describe("billingFromApi", () => {
  it("maps GET /v1/billing, with no credit expiry and no linked project", () => {
    const b = billingFromApi({
      credits: { granted_usd: 0, used_usd: 1.25, expires_at: null },
      month_to_date_usd: 3.5,
      forecast_usd: 10.85,
      by_app: [{ app: "hello", billing: "shipcube", compute_usd: 1, data_usd: 0.5, llm_usd: 2 }],
      daily: [{ date: "2026-10-29", cost_usd: 0.4 }],
      gcp: null,
      source: "usage_daily",
    });
    expect(b).toEqual({
      credits: { granted: 0, used: 1.25, expiresAt: undefined },
      gcp: { linked: false },
      monthToDate: 3.5,
      forecast: 10.85,
      byApp: [{ appId: "hello", name: "hello", billing: "shipcube", compute: 1, data: 0.5, llm: 2 }],
      daily: [{ t: "2026-10-29", value: 0.4 }],
    });
  });
});

describe("summaryFromApi", () => {
  it("keeps soft-deleted apps, marked, for the Recently deleted list", () => {
    const base = { name: "old", url: "https://old.run.app", tier: "T1", status: "running", updated_at: "2026-10-28T00:00:00Z" };
    expect(summaryFromApi({ ...base, target: "shipcube", deleted_at: "2026-10-29T00:00:00Z" })).toMatchObject({
      id: "old",
      health: "down",
      billing: "shipcube",
      deletedAt: "2026-10-29T00:00:00Z",
    });
    expect(summaryFromApi({ ...base, target: "acme-prod" })).toMatchObject({ billing: "own-gcp", deletedAt: undefined });
    expect(summaryFromApi({ ...base, target: "local" }).billing).toBeUndefined();
  });
});

describe("waitlistFromApi", () => {
  it("maps an admin list row", () => {
    expect(
      waitlistFromApi({
        id: "wl_1", email: "a@example.com", name: "A", use_case: "A dashboard", status: "pending",
        created_at: "2026-10-31T10:00:00Z", decided_at: null, decided_by: null,
      }),
    ).toEqual({ id: "wl_1", email: "a@example.com", name: "A", useCase: "A dashboard", status: "pending", createdAt: "2026-10-31T10:00:00Z", decidedAt: undefined, decidedBy: undefined, workspaceId: undefined });
  });
});

describe("accountStatusFrom", () => {
  const refuse = (code: string, status: number) => () => Promise.reject(new ApiError(code, code, status, "r"));
  const onList = async () => ({ email: "a@example.com", status: "pending" as const, requested_at: "2026-10-31T10:00:00Z", platform_admin: false });

  it("is a member when /v1/me answers, with the admin flag", async () => {
    expect(await accountStatusFrom(async () => ({ platform_admin: true }), onList)).toEqual({ member: true, platformAdmin: true });
    expect(await accountStatusFrom(async () => ({}), onList)).toEqual({ member: true, platformAdmin: false });
  });

  it("asks /v1/waitlist/me when /v1/me refuses a session with no workspace", async () => {
    expect(await accountStatusFrom(refuse("unauthenticated", 401), onList)).toEqual({
      member: false,
      platformAdmin: false,
      waitlist: "pending",
      requestedAt: "2026-10-31T10:00:00Z",
    });
    const admin = async () => ({ email: "k@example.com", status: "none" as const, requested_at: null, platform_admin: true });
    expect(await accountStatusFrom(refuse("unauthenticated", 401), admin)).toMatchObject({ member: false, platformAdmin: true, waitlist: "none" });
  });

  it("never sends a member to the waitlist because the API failed", async () => {
    await expect(accountStatusFrom(refuse("unavailable", 503), onList)).rejects.toThrow("unavailable");
    await expect(accountStatusFrom(refuse("permission_denied", 403), onList)).rejects.toThrow("permission_denied");
  });

  it("treats an API without the waitlist as no request yet, and a dead session as an error", async () => {
    expect(await accountStatusFrom(refuse("unauthenticated", 401), refuse("not_found", 404))).toEqual({ member: false, platformAdmin: false, waitlist: "none" });
    await expect(accountStatusFrom(refuse("unauthenticated", 401), refuse("unauthenticated", 401))).rejects.toThrow();
  });
});
