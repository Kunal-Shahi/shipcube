import { afterEach, describe, expect, it, vi } from "vitest";
import { DEV_MCP_URL, config, mockDataEnabled, resetConfig } from "./config";

function withEnv(env: Record<string, string>) {
  for (const k of ["DEV_MODE", "AUTH_PROVIDER", "K_SERVICE", "K_REVISION", "CLOUD_RUN_JOB", "SHIPCUBE_DEV_MODE", "SHIPCUBE_DEV_API_SESSION", "SHIPCUBE_API_URL", "IDENTITY_PLATFORM_PROJECT", "NEXT_PUBLIC_FIREBASE_PROJECT_ID", "SHIPCUBE_MCP_URL", "FIREBASE_AUTH_EMULATOR_HOST"]) vi.stubEnv(k, "");
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

  it("requires an https MCP URL in production, with no built-in default", () => {
    const prod = { NODE_ENV: "production", SHIPCUBE_API_URL: "https://api", IDENTITY_PLATFORM_PROJECT: "p" };
    withEnv(prod);
    expect(() => config()).toThrow(/SHIPCUBE_MCP_URL is required/);
    withEnv({ ...prod, SHIPCUBE_MCP_URL: "http://mcp.example.com/mcp" });
    expect(() => config()).toThrow(/must use https/);
    withEnv({ ...prod, SHIPCUBE_MCP_URL: "not a url" });
    expect(() => config()).toThrow(/isn't a URL/);
  });

  it("defaults the MCP URL to the local server in DEV_MODE", () => {
    withEnv({ DEV_MODE: "1" });
    expect(config().mcpUrl).toBe(DEV_MCP_URL);
  });

  it("uses the Firebase Auth emulator only in DEV_MODE, never on Cloud Run", () => {
    withEnv({ DEV_MODE: "1", AUTH_PROVIDER: "firebase-emulator" });
    expect(config()).toMatchObject({ auth: "firebase-emulator", authEmulatorHost: "127.0.0.1:9099", identityProject: "demo-shipcube" });
    withEnv({ AUTH_PROVIDER: "firebase-emulator", SHIPCUBE_API_URL: "https://api", SHIPCUBE_MCP_URL: "https://mcp/mcp" });
    expect(() => config()).toThrow(/requires DEV_MODE/);
    withEnv({ DEV_MODE: "1", AUTH_PROVIDER: "firebase-emulator", K_SERVICE: "c" });
    expect(() => config()).toThrow(/refused on Cloud Run/);
  });

  it("refuses FIREBASE_AUTH_EMULATOR_HOST outside DEV_MODE, whatever the provider", () => {
    withEnv({ SHIPCUBE_API_URL: "https://api", SHIPCUBE_MCP_URL: "https://mcp/mcp", IDENTITY_PLATFORM_PROJECT: "p", FIREBASE_AUTH_EMULATOR_HOST: "127.0.0.1:9099" });
    expect(() => config()).toThrow(/unsigned tokens/);
  });

  it("refuses the mock sign-in outside DEV_MODE", () => {
    withEnv({ AUTH_PROVIDER: "mock", SHIPCUBE_API_URL: "https://api" });
    expect(() => config()).toThrow(/requires DEV_MODE/);
  });

  it("uses Identity Platform and the API in production", () => {
    withEnv({ SHIPCUBE_API_URL: "https://api/", SHIPCUBE_MCP_URL: "https://mcp/mcp", IDENTITY_PLATFORM_PROJECT: "shipcube-control-ks", K_SERVICE: "c" });
    expect(config()).toMatchObject({ auth: "identity-platform", apiUrl: "https://api", mcpUrl: "https://mcp/mcp", devMode: false });
    expect(mockDataEnabled()).toBe(false);
  });

  it("serves mock data only in DEV_MODE without an API URL", () => {
    withEnv({ DEV_MODE: "1" });
    expect(mockDataEnabled()).toBe(true);
    withEnv({ DEV_MODE: "1", SHIPCUBE_API_URL: "http://127.0.0.1:8080" });
    expect(mockDataEnabled()).toBe(false);
  });

  // #29: what the deployed console sets today (deploy/services/console.yaml, live Oct 31) must start.
  const LIVE = {
    K_SERVICE: "shipcube-console",
    SHIPCUBE_API_URL: "https://shipcube-mcp-340481939948.asia-south1.run.app",
    SHIPCUBE_API_TIMEOUT_MS: "30000",
    SHIPCUBE_MCP_URL: "https://shipcube-mcp-340481939948.asia-south1.run.app/mcp",
    IDENTITY_PLATFORM_PROJECT: "shipcube-control-ks",
  };

  it("starts with the live console's settings", () => {
    withEnv(LIVE);
    expect(config()).toMatchObject({ onCloudRun: true, devMode: false, auth: "identity-platform" });
  });

  it("treats any Cloud Run variable as Cloud Run", () => {
    for (const k of ["K_REVISION", "CLOUD_RUN_JOB"]) {
      withEnv({ ...LIVE, K_SERVICE: "", [k]: "x", DEV_MODE: "1" });
      expect(() => config()).toThrow(/refused on Cloud Run/);
    }
  });

  it("refuses the API's dev switch and a local console session on Cloud Run", () => {
    withEnv({ ...LIVE, SHIPCUBE_DEV_MODE: "1" });
    expect(() => config()).toThrow(/SHIPCUBE_DEV_MODE is refused/);
    withEnv({ ...LIVE, SHIPCUBE_DEV_API_SESSION: "sc_ses_abcdefgh_x" });
    expect(() => config()).toThrow(/SHIPCUBE_DEV_API_SESSION/);
  });

  it("refuses local or plain-http API and MCP URLs on Cloud Run, whatever NODE_ENV says", () => {
    vi.stubEnv("NODE_ENV", "development");
    for (const [k, v] of [
      ["SHIPCUBE_API_URL", "http://127.0.0.1:8080"],
      ["SHIPCUBE_API_URL", "https://localhost"],
      ["SHIPCUBE_MCP_URL", "http://shipcube-mcp.example.com/mcp"],
      ["SHIPCUBE_MCP_URL", "https://web.localhost/mcp"],
    ]) {
      withEnv({ ...LIVE, [k]: v });
      expect(() => config()).toThrow(new RegExp(`${k} .* must be a public https URL on Cloud Run`));
    }
  });
});
