import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/errors";

const requestAccess = vi.fn();
const getSession = vi.fn();
const headerValues = new Map<string, string>();

vi.mock("next/headers", () => ({ headers: async () => ({ get: (k: string) => headerValues.get(k) ?? null }) }));
vi.mock("@/lib/auth", () => ({ auth: { getSession: () => getSession() } }));
vi.mock("@/lib/server/data", () => ({ data: async () => ({ requestAccess }) }));
vi.mock("@/lib/server/log", () => ({ log: () => {} }));

const { requestAccessAction } = await import("./actions");

function form(fields: Record<string, string>) {
  const f = new FormData();
  for (const [k, v] of Object.entries(fields)) f.set(k, v);
  return f;
}
const valid = { email: "ada@example.com", name: "Ada", useCase: "A cost dashboard for ops." };

beforeEach(() => {
  requestAccess.mockReset();
  getSession.mockReset().mockResolvedValue(null);
  headerValues.clear();
});

describe("requestAccessAction", () => {
  it("sends a valid request with the visitor's IP", async () => {
    headerValues.set("x-forwarded-for", "203.0.113.7, 10.0.0.1");
    expect(await requestAccessAction({ status: "idle" }, form(valid))).toEqual({ status: "sent" });
    expect(requestAccess).toHaveBeenCalledWith(valid, "203.0.113.7");
  });

  it("answers a filled honeypot like a success, and sends nothing", async () => {
    expect(await requestAccessAction({ status: "idle" }, form({ ...valid, website: "http://spam.example" }))).toEqual({ status: "sent" });
    expect(requestAccess).not.toHaveBeenCalled();
  });

  it("returns field errors and keeps what was typed", async () => {
    const r = await requestAccessAction({ status: "idle" }, form({ ...valid, email: "nope" }));
    expect(r).toMatchObject({ status: "error", errors: { email: expect.stringMatching(/email address/) }, values: { name: "Ada" } });
    expect(requestAccess).not.toHaveBeenCalled();
  });

  it("uses the signed-in account's email, not the form's", async () => {
    getSession.mockResolvedValue({ user: { email: "me@example.com" } });
    await requestAccessAction({ status: "idle" }, form({ ...valid, email: "someone-else@example.com" }));
    expect(requestAccess.mock.calls[0][0].email).toBe("me@example.com");
  });

  it("explains a rate limit, and hides other API failures behind a retry message", async () => {
    requestAccess.mockRejectedValueOnce(new ApiError("rate_limited", "slow down", 429, "r"));
    expect(await requestAccessAction({ status: "idle" }, form(valid))).toMatchObject({ status: "error", message: expect.stringMatching(/Too many requests/) });
    requestAccess.mockRejectedValueOnce(new ApiError("unavailable", "db down at 10.1.2.3", 503, "r"));
    const r = await requestAccessAction({ status: "idle" }, form(valid));
    expect(r).toMatchObject({ status: "error", message: "We couldn't save your request just now. Try again in a minute." });
  });
});
