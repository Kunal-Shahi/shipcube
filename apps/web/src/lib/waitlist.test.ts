import { describe, expect, it, vi } from "vitest";
import { TERMS_VERSION } from "./legal";
import { LIMITS, submitAccessRequest, validateAccessRequest } from "./waitlist";

const ok = { email: "ada@example.com", name: "Ada", useCase: "A cost dashboard for ops.", consent: true };

describe("validateAccessRequest", () => {
  it("accepts a complete request", () => {
    expect(validateAccessRequest(ok)).toEqual({});
  });

  it("names each missing or malformed field", () => {
    expect(validateAccessRequest({ email: "", name: " ", useCase: "", consent: false })).toEqual({
      email: "Enter your work email.",
      name: "Enter your name.",
      useCase: "Say a little about what you'd deploy.",
      consent: "Agree to the Terms and Privacy notice to request access.",
    });
    expect(validateAccessRequest({ ...ok, email: "ada@example" }).email).toMatch(/email address/);
  });

  it("needs the Terms and Privacy notice agreed to", () => {
    expect(validateAccessRequest({ ...ok, consent: false })).toEqual({ consent: "Agree to the Terms and Privacy notice to request access." });
  });

  it("enforces the length limits the API also enforces", () => {
    expect(validateAccessRequest({ ...ok, name: "x".repeat(LIMITS.name + 1) }).name).toMatch(/under 100/);
    expect(validateAccessRequest({ ...ok, useCase: "x".repeat(LIMITS.useCase + 1) }).useCase).toMatch(/under 500/);
  });
});

describe("submitAccessRequest", () => {
  const reply = (status: number, body: unknown = {}, headers: Record<string, string> = {}) =>
    vi.fn(async () => new Response(JSON.stringify(body), { status, headers }));
  const fields = { ...ok, honeypot: "" };

  it("posts the API's field names and the terms version, without cookies, and treats 202 as done", async () => {
    const f = reply(202, { status: "received" });
    expect(await submitAccessRequest("https://api.example", fields, f)).toEqual({ ok: true });
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://api.example/v1/waitlist");
    expect(init.credentials).toBe("omit");
    expect(JSON.parse(String(init.body))).toEqual({ email: ok.email, name: ok.name, use_case: ok.useCase, terms_version: TERMS_VERSION, website: "" });
  });

  it("passes a filled honeypot through for the API to drop quietly", async () => {
    const f = reply(202);
    await submitAccessRequest("https://api.example", { ...fields, honeypot: "spam" }, f);
    expect(JSON.parse(String((f.mock.calls[0] as unknown as [string, RequestInit])[1].body)).website).toBe("spam");
  });

  it("says how long to wait when rate-limited", async () => {
    const r = await submitAccessRequest("https://api.example", fields, reply(429, { error: { code: "rate_limited", retry_after: 1800 } }));
    expect(r).toEqual({ ok: false, message: "Too many requests from here. Try again in 30 minutes." });
    const h = await submitAccessRequest("https://api.example", fields, reply(429, {}, { "Retry-After": "45" }));
    expect(h).toEqual({ ok: false, message: "Too many requests from here. Try again in 45 seconds." });
  });

  it("offers sign-in when the global anonymous budget is spent, not a per-IP wait", async () => {
    const busy = await submitAccessRequest("https://api.example", fields, reply(429, { error: { code: "waitlist_busy", retry_after: 600 } }, { "Retry-After": "600" }));
    expect(busy).toEqual({ ok: false, message: "We're getting a lot of requests. Sign in with Google to request access.", signIn: true });
    const perIp = await submitAccessRequest("https://api.example", fields, reply(429, { error: { code: "rate_limited", retry_after: 600 } }));
    expect(perIp).toEqual({ ok: false, message: "Too many requests from here. Try again in 10 minutes." });
  });

  it("shows the API's reason for a bad field, and a plain retry message otherwise", async () => {
    const bad = await submitAccessRequest("https://api.example", fields, reply(400, { error: { code: "invalid_argument", message: "name is too long" } }));
    expect(bad).toEqual({ ok: false, message: "name is too long" });
    const down = await submitAccessRequest("https://api.example", fields, reply(503, { error: { message: "db at 10.0.0.3" } }));
    expect(down).toEqual({ ok: false, message: "We couldn't save your request just now. Try again in a minute." });
    const offline = await submitAccessRequest("https://api.example", fields, vi.fn(async () => Promise.reject(new TypeError("fetch failed"))));
    expect(offline.ok).toBe(false);
  });
});
