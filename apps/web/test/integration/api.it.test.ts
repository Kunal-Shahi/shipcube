import { beforeAll, describe, expect, it, vi } from "vitest";

// Runs the console's HTTP data source against a live control-plane API.
//   SHIPCUBE_IT_API_URL=http://127.0.0.1:8081 SHIPCUBE_IT_SESSION=sc_ses_… npm run test:integration
// Skipped when those aren't set. Against a local services/api: start it with
// SHIPCUBE_DEV_CONSOLE_TOKEN set to the same session token (see services/api/README.md).

const url = process.env.SHIPCUBE_IT_API_URL;
const session = process.env.SHIPCUBE_IT_SESSION;

describe.skipIf(!url || !session)("HTTP data source against the control-plane API", () => {
  let data: typeof import("@/lib/server/data/http").httpData;

  beforeAll(async () => {
    vi.stubEnv("SHIPCUBE_API_URL", url!);
    vi.stubEnv("DEV_MODE", "1");
    vi.stubEnv("AUTH_PROVIDER", "mock");
    vi.stubEnv("K_SERVICE", "");
    vi.spyOn(console, "log").mockImplementation(() => {});
    const { resetConfig } = await import("@/lib/server/config");
    resetConfig();
    const { setApiCredential } = await import("@/lib/server/http");
    setApiCredential(async () => session!);
    data = (await import("@/lib/server/data/http")).httpData;
  });

  it("lists apps and reads one with caps, access, logs and metrics", async () => {
    const apps = await data.listApps();
    expect(apps.length).toBeGreaterThan(0);
    const app = await data.getApp(apps[0].id);
    expect(app).not.toBeNull();
    expect(app!.caps.memory).toMatch(/^\d+ (Mi|Gi)B$/);
    expect(Array.isArray(app!.access)).toBe(true);
    expect(Array.isArray(app!.logs)).toBe(true);
  });

  it("returns null for an app that doesn't exist", async () => {
    expect(await data.getApp("no-such-app-zz")).toBeNull();
  });

  it("parks a cap raise for approval, and a console session can approve it", async () => {
    const [first] = await data.listApps();
    const before = (await data.getApp(first.id))!.caps.monthlySpend;
    const r = await data.saveCaps(first.id, { monthlySpend: before + 500 }, `it-${Date.now()}`);
    expect(r.approval?.url).toMatch(/^\/approvals\//);

    const pending = await data.getApproval(r.approval!.id);
    expect(pending).toMatchObject({ status: "pending", kind: "cap_raise" });

    const decided = await data.decideApproval(r.approval!.id, "reject", "integration test");
    expect(decided?.status).toBe("rejected");
    expect((await data.getApp(first.id))!.caps.monthlySpend).toBe(before);
  });
});
