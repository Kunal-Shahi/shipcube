import { afterEach, describe, expect, it, vi } from "vitest";
import { register } from "./instrumentation";
import { resetConfig } from "./lib/server/config";

const PROD = { NODE_ENV: "production", NEXT_RUNTIME: "nodejs", SHIPCUBE_API_URL: "https://api", IDENTITY_PLATFORM_PROJECT: "p", K_SERVICE: "c" };

function withEnv(env: Record<string, string>) {
  for (const k of ["DEV_MODE", "AUTH_PROVIDER", "NEXT_PHASE", "SHIPCUBE_MCP_URL"]) vi.stubEnv(k, "");
  for (const [k, v] of Object.entries(env)) vi.stubEnv(k, v);
  resetConfig();
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  resetConfig();
});

describe("register() at production boot", () => {
  it("exits when SHIPCUBE_MCP_URL is unset", async () => {
    withEnv(PROD);
    const exit = vi.spyOn(process, "exit").mockImplementation((() => undefined) as never);
    const log = vi.spyOn(console, "error").mockImplementation(() => {});
    await register();
    expect(exit).toHaveBeenCalledWith(1);
    expect(log.mock.calls[0][0]).toMatch(/SHIPCUBE_MCP_URL is required/);
  });

  it("exits when SHIPCUBE_MCP_URL isn't https", async () => {
    withEnv({ ...PROD, SHIPCUBE_MCP_URL: "http://shipcube-mcp.example.com/mcp" });
    const exit = vi.spyOn(process, "exit").mockImplementation((() => undefined) as never);
    vi.spyOn(console, "error").mockImplementation(() => {});
    await register();
    expect(exit).toHaveBeenCalledWith(1);
  });

  it("starts with a valid https URL", async () => {
    withEnv({ ...PROD, SHIPCUBE_MCP_URL: "https://shipcube-mcp-123.asia-south1.run.app/mcp" });
    const exit = vi.spyOn(process, "exit").mockImplementation((() => undefined) as never);
    await register();
    expect(exit).not.toHaveBeenCalled();
  });

  it("doesn't check during next build", async () => {
    withEnv({ ...PROD, NEXT_PHASE: "phase-production-build" });
    const exit = vi.spyOn(process, "exit").mockImplementation((() => undefined) as never);
    await register();
    expect(exit).not.toHaveBeenCalled();
  });
});
