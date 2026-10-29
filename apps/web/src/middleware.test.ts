import { afterEach, describe, expect, it, vi } from "vitest";
import { csp } from "./middleware";

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
