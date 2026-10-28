import { describe, expect, it } from "vitest";
import { approvalFromOperation, capsFromApi, capsToApi, grantFromApi, healthOf, type ApiOperation } from "./http";

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
