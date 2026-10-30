import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { csp, isPublic, middleware } from "./middleware";

afterEach(() => vi.unstubAllEnvs());

describe("csp()", () => {
  it("allows only Google's sign-in hosts in production", () => {
    vi.stubEnv("AUTH_PROVIDER", "");
    const p = csp("n", false);
    expect(p).not.toContain("http://");
    expect(p).toContain("upgrade-insecure-requests");
  });

  it("allows the browser-facing auth emulator when it's the provider", () => {
    vi.stubEnv("AUTH_PROVIDER", "firebase-emulator");
    vi.stubEnv("FIREBASE_AUTH_EMULATOR_HOST", "auth-emulator:9099");
    vi.stubEnv("FIREBASE_AUTH_EMULATOR_PUBLIC_URL", "http://localhost:9099");
    const p = csp("n", false);
    expect(p).toMatch(/connect-src [^;]*http:\/\/localhost:9099/);
    expect(p).toMatch(/frame-src [^;]*http:\/\/localhost:9099/);
    expect(p).not.toContain("auth-emulator");
    expect(p).not.toContain("upgrade-insecure-requests");
  });
});

describe("which routes need a session", () => {
  it("leaves only the landing page, sign-in and the auth endpoints public", () => {
    for (const p of ["/", "/opengraph-image", "/sign-in", "/sign-up", "/api/auth/session", "/api/healthz"]) expect(isPublic(p), p).toBe(true);
  });

  it("protects every console route, the waitlist page and the admin page", () => {
    for (const p of ["/apps", "/apps/x", "/approvals", "/settings/tokens", "/billing", "/data", "/setup", "/onboarding", "/waitlist", "/admin/waitlist", "/x", "//apps", "/opengraph-image/x"]) {
      expect(isPublic(p), p).toBe(false);
    }
  });

  it("sends a signed-out visitor to sign-in, and lets the landing page through", () => {
    const admin = middleware(new NextRequest("http://localhost/admin/waitlist"));
    expect(admin.status).toBe(307);
    expect(admin.headers.get("location")).toBe("http://localhost/sign-in?returnTo=%2Fadmin%2Fwaitlist");
    const landing = middleware(new NextRequest("http://localhost/"));
    expect(landing.headers.get("location")).toBeNull();
    expect(landing.headers.get("content-security-policy")).toContain("frame-ancestors 'none'");
  });
});
