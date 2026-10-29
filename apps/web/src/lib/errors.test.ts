import { describe, expect, it } from "vitest";
import { describeError } from "./errors";

const denied = (message: string) => describeError({ code: "permission_denied", message, requestId: "r" });

describe("describeError: permission_denied", () => {
  it("shows the API's reason when it gives one", () => {
    const why = "The person who proposed this migration can't apply it. Ask another workspace member to apply it.";
    expect(denied(why)).toBe(why);
  });

  it("falls back to the role message when the API only restates the status", () => {
    for (const m of ["403 Forbidden", "Forbidden", "permission denied", ""]) {
      expect(denied(m)).toMatch(/Your role in this workspace/);
    }
  });
});
