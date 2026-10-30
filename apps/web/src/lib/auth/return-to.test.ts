import { describe, expect, it } from "vitest";
import { safeReturnTo } from "./return-to";

const O = "https://shipcube.web.app";

describe("safeReturnTo", () => {
  it("keeps paths on this origin", () => {
    expect(safeReturnTo("/apps", O)).toBe("/apps");
    expect(safeReturnTo("/admin/waitlist?x=1#y", O)).toBe("/admin/waitlist?x=1#y");
  });

  it.each([
    "//evil.example", "/\\evil.example", "/\\/evil.example", "/%5Cevil.example".replace("%5C", "\\"),
    "/.//evil.example", "/x/..//evil.example", "/%2e//evil.example", "/.\\/evil.example",
    "https://evil.example", "javascript:alert(1)", "evil.example", "", "\\\\evil.example",
  ])("refuses %s", (to) => {
    expect(safeReturnTo(to, O)).toBe("/apps");
  });

  it("falls back when missing", () => {
    expect(safeReturnTo(undefined, O)).toBe("/apps");
    expect(safeReturnTo(null, O, "/onboarding")).toBe("/onboarding");
  });
});
