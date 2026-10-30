import { describe, expect, it } from "vitest";
import { LIMITS, validateAccessRequest } from "./waitlist";

const ok = { email: "ada@example.com", name: "Ada", useCase: "A cost dashboard for ops." };

describe("validateAccessRequest", () => {
  it("accepts a complete request", () => {
    expect(validateAccessRequest(ok)).toEqual({});
  });

  it("names each missing or malformed field", () => {
    expect(validateAccessRequest({ email: "", name: " ", useCase: "" })).toEqual({
      email: "Enter your work email.",
      name: "Enter your name.",
      useCase: "Say a little about what you'd deploy.",
    });
    expect(validateAccessRequest({ ...ok, email: "ada@example" }).email).toMatch(/email address/);
  });

  it("enforces the length limits the API also enforces", () => {
    expect(validateAccessRequest({ ...ok, name: "x".repeat(LIMITS.name + 1) }).name).toMatch(/under 100/);
    expect(validateAccessRequest({ ...ok, useCase: "x".repeat(LIMITS.useCase + 1) }).useCase).toMatch(/under 1000/);
  });
});
