import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/errors";
import { resetConfig } from "./config";
import { api, backoffMs, isIdempotent } from "./http";

const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });

describe("api()", () => {
  beforeEach(() => {
    vi.stubEnv("SHIPCUBE_API_URL", "https://api.test");
    vi.stubEnv("DEV_MODE", "1");
    vi.stubEnv("AUTH_PROVIDER", "mock");
    vi.stubEnv("K_SERVICE", "");
    resetConfig();
    vi.spyOn(console, "log").mockImplementation(() => {});
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(Math, "random").mockReturnValue(0); // shortest backoff, so retries take ~125 ms
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("retries an idempotent GET on 503 and returns the later success", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(json(503, { error: { code: "unavailable", message: "down" } })).mockResolvedValueOnce(json(200, { apps: [] }));
    vi.stubGlobal("fetch", fetch);
    const p = api<{ apps: unknown[] }>("/v1/apps");
    await expect(p).resolves.toEqual({ apps: [] });
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("doesn't retry a POST without an idempotency key", async () => {
    const fetch = vi.fn().mockResolvedValue(json(503, { error: { code: "unavailable", message: "down" } }));
    vi.stubGlobal("fetch", fetch);
    await expect(api("/v1/apps/x/restore", { method: "POST" })).rejects.toMatchObject({ code: "unavailable", status: 503 });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("retries a PATCH that carries an idempotency key, sending the same key", async () => {
    const fetch = vi.fn().mockRejectedValueOnce(new TypeError("fetch failed")).mockResolvedValueOnce(json(200, { caps: {} }));
    vi.stubGlobal("fetch", fetch);
    const p = api("/v1/apps/x/caps", { method: "PATCH", body: {}, idempotencyKey: "caps-1" });
    await p;
    expect(fetch).toHaveBeenCalledTimes(2);
    for (const call of fetch.mock.calls) expect(call[1].headers["Idempotency-Key"]).toBe("caps-1");
  });

  it("doesn't retry client errors and keeps the API's error code and message", async () => {
    const fetch = vi.fn().mockResolvedValue(json(409, { error: { code: "conflict", message: "already decided" } }));
    vi.stubGlobal("fetch", fetch);
    const err = await api("/v1/apps").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ code: "conflict", message: "already decided", retryable: false });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("reports a timeout as a retryable timeout error", async () => {
    const fetch = vi.fn().mockRejectedValue(new DOMException("timed out", "TimeoutError"));
    vi.stubGlobal("fetch", fetch);
    const p = api("/v1/apps", { attempts: 2 }).catch((e) => e);
    const err = await p;
    expect(err).toMatchObject({ code: "timeout", retryable: true });
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("sends a request id and the query string", async () => {
    const fetch = vi.fn().mockResolvedValue(json(200, { entries: [] }));
    vi.stubGlobal("fetch", fetch);
    await api("/v1/apps/x/logs", { query: { limit: 50, page_token: undefined } });
    const [url, init] = fetch.mock.calls[0];
    expect(String(url)).toBe("https://api.test/v1/apps/x/logs?limit=50");
    expect(init.headers["X-Request-Id"]).toMatch(/[0-9a-f-]{36}/);
  });
});

describe("retry policy", () => {
  it("treats GET, PUT, DELETE and keyed requests as idempotent", () => {
    expect(isIdempotent("GET")).toBe(true);
    expect(isIdempotent("PUT")).toBe(true);
    expect(isIdempotent("POST")).toBe(false);
    expect(isIdempotent("POST", "k")).toBe(true);
  });

  it("honours Retry-After up to 10 s and caps exponential backoff at 4 s", () => {
    expect(backoffMs(0, 2)).toBe(2000);
    expect(backoffMs(0, 60)).toBe(10_000);
    for (let i = 0; i < 20; i++) expect(backoffMs(10)).toBeLessThanOrEqual(4000);
  });
});
