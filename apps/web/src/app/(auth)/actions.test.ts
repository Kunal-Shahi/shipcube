import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/errors";
import { TERMS_VERSION } from "@/lib/legal";

const requestAccessSignedIn = vi.fn();
const getSession = vi.fn();
vi.mock("next/navigation", () => ({ redirect: vi.fn() }));
vi.mock("@/lib/auth", () => ({ auth: { getSession: () => getSession(), signOut: vi.fn() } }));
vi.mock("@/lib/server/data", () => ({ data: async () => ({ requestAccessSignedIn }) }));
vi.mock("@/lib/server/log", () => ({ log: () => {} }));

const { requestAccessSignedInAction } = await import("./actions");

beforeEach(() => {
  requestAccessSignedIn.mockReset();
  getSession.mockReset().mockResolvedValue({ user: { email: "me@example.com" } });
});

describe("requestAccessSignedInAction", () => {
  it("sends what the person typed and the terms version; the API takes the email from the session", async () => {
    expect(await requestAccessSignedInAction({ name: " Ada ", useCase: " A dashboard ", consent: true })).toEqual({ ok: true });
    expect(requestAccessSignedIn).toHaveBeenCalledWith({ name: "Ada", useCase: "A dashboard", termsVersion: TERMS_VERSION });
  });

  it("refuses without a session, and checks the fields first", async () => {
    getSession.mockResolvedValueOnce(null);
    expect(await requestAccessSignedInAction({ name: "Ada", useCase: "x", consent: true })).toMatchObject({ ok: false, message: expect.stringMatching(/Sign in again/) });
    expect(await requestAccessSignedInAction({ name: "", useCase: "x", consent: true })).toEqual({ ok: false, message: "Enter your name." });
    expect(await requestAccessSignedInAction({ name: "Ada", useCase: "x", consent: false })).toEqual({
      ok: false,
      message: "Agree to the Terms and Privacy notice to request access.",
    });
    expect(requestAccessSignedIn).not.toHaveBeenCalled();
  });

  it("shows the API's reason for a bad field, and hides other failures", async () => {
    requestAccessSignedIn.mockRejectedValueOnce(new ApiError("invalid_argument", "use_case is too long", 400, "r"));
    expect(await requestAccessSignedInAction({ name: "Ada", useCase: "x", consent: true })).toEqual({ ok: false, message: "use_case is too long" });
    requestAccessSignedIn.mockRejectedValueOnce(new ApiError("rate_limited", "slow down", 429, "r"));
    expect(await requestAccessSignedInAction({ name: "Ada", useCase: "x", consent: true })).toEqual({ ok: false, message: "Too many requests right now. Try again in a little while." });
    requestAccessSignedIn.mockRejectedValueOnce(new ApiError("unavailable", "db at 10.0.0.3", 503, "r"));
    expect(await requestAccessSignedInAction({ name: "Ada", useCase: "x", consent: true })).toEqual({ ok: false, message: "We couldn't save your request just now. Try again in a minute." });
  });
});
