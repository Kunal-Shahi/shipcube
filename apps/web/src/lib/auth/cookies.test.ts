import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
vi.mock("next/headers", () => ({ cookies: vi.fn() }));

const { splitSessionValue, ONBOARDED_SUFFIX } = await import("./cookies");

describe("splitSessionValue", () => {
  it("separates the onboarded hint from the session value", () => {
    expect(splitSessionValue(`eyJ.a.b${ONBOARDED_SUFFIX}`)).toEqual({ session: "eyJ.a.b", onboarded: true });
    expect(splitSessionValue("eyJ.a.b")).toEqual({ session: "eyJ.a.b", onboarded: false });
  });

  it("treats missing or suffix-only values as no session", () => {
    expect(splitSessionValue(undefined)).toEqual({ session: null, onboarded: false });
    expect(splitSessionValue("")).toEqual({ session: null, onboarded: false });
    expect(splitSessionValue(ONBOARDED_SUFFIX)).toEqual({ session: null, onboarded: true });
  });
});
