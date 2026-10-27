import { afterEach, describe, expect, it, vi } from "vitest";
import { config, mockDataEnabled, resetConfig } from "./config";

function withEnv(env: Record<string, string>) {
  for (const k of ["DEV_MODE", "AUTH_PROVIDER", "K_SERVICE", "SHIPCUBE_API_URL", "IDENTITY_PLATFORM_PROJECT", "NEXT_PUBLIC_FIREBASE_PROJECT_ID"]) vi.stubEnv(k, "");
  for (const [k, v] of Object.entries(env)) vi.stubEnv(k, v);
  resetConfig();
}

afterEach(() => {
  vi.unstubAllEnvs();
  resetConfig();
});

describe("config()", () => {
  it("refuses DEV_MODE on Cloud Run", () => {
    withEnv({ DEV_MODE: "1", K_SERVICE: "shipcube-console" });
    expect(() => config()).toThrow(/refused on Cloud Run/);
  });

  it("requires an API URL outside DEV_MODE", () => {
    withEnv({ IDENTITY_PLATFORM_PROJECT: "p" });
    expect(() => config()).toThrow(/SHIPCUBE_API_URL/);
  });

  it("refuses the mock sign-in outside DEV_MODE", () => {
    withEnv({ AUTH_PROVIDER: "mock", SHIPCUBE_API_URL: "https://api" });
    expect(() => config()).toThrow(/requires DEV_MODE/);
  });

  it("uses Identity Platform and the API in production", () => {
    withEnv({ SHIPCUBE_API_URL: "https://api/", IDENTITY_PLATFORM_PROJECT: "shipcube-control-ks", K_SERVICE: "c" });
    expect(config()).toMatchObject({ auth: "identity-platform", apiUrl: "https://api", devMode: false });
    expect(mockDataEnabled()).toBe(false);
  });

  it("serves mock data only in DEV_MODE without an API URL", () => {
    withEnv({ DEV_MODE: "1" });
    expect(mockDataEnabled()).toBe(true);
    withEnv({ DEV_MODE: "1", SHIPCUBE_API_URL: "http://127.0.0.1:8080" });
    expect(mockDataEnabled()).toBe(false);
  });
});
