import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/errors";

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
  it("sends only what the person typed; the API takes the email from the session", async () => {
    expect(await requestAccessSignedInAction({ name: " Ada ", useCase: " A dashboard " })).toEqual({ ok: true });
    expect(requestAccessSignedIn).toHaveBeenCalledWith({ name: "Ada", useCase: "A dashboard" });
  });

  it("refuses without a session, and checks the fields first", async () => {
    getSession.mockResolvedValueOnce(null);
    expect(await requestAccessSignedInAction({ name: "Ada", useCase: "x" })).toMatchObject({ ok: false, message: expect.stringMatching(/Sign in again/) });
    expect(await requestAccessSignedInAction({ name: "", useCase: "x" })).toEqual({ ok: false, message: "Enter your name." });
    expect(requestAccessSignedIn).not.toHaveBeenCalled();
  });

  it("shows the API's reason for a bad field, and hides other failures", async () => {
    requestAccessSignedIn.mockRejectedValueOnce(new ApiError("invalid_argument", "use_case is too long", 400, "r"));
    expect(await requestAccessSignedInAction({ name: "Ada", useCase: "x" })).toEqual({ ok: false, message: "use_case is too long" });
    requestAccessSignedIn.mockRejectedValueOnce(new ApiError("unavailable", "db at 10.0.0.3", 503, "r"));
    expect(await requestAccessSignedInAction({ name: "Ada", useCase: "x" })).toEqual({ ok: false, message: "We couldn't save your request just now. Try again in a minute." });
  });
});
