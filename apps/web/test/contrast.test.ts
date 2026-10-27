import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// WCAG 2.1 AA contrast for the design tokens in globals.css, in both themes.
const css = readFileSync(new URL("../src/app/globals.css", import.meta.url), "utf8");

function block(selector: string) {
  const start = css.indexOf(selector + " {");
  const body = css.slice(start, css.indexOf("}", start));
  return Object.fromEntries([...body.matchAll(/--([\w-]+):\s*(#[0-9a-f]{6})/gi)].map((m) => [m[1], m[2]]));
}

function luminance(hex: string) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function ratio(a: string, b: string) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const themes = { light: block(":root"), dark: block(':root[data-theme="dark"]') };

const TEXT: [string, string][] = [
  ["fg", "bg"], ["fg", "surface"], ["fg", "surface-2"],
  ["fg-muted", "bg"], ["fg-muted", "surface"], ["fg-muted", "surface-2"],
  ["fg-subtle", "bg"], ["fg-subtle", "surface"], ["fg-subtle", "surface-2"],
  ["accent", "bg"], ["accent", "surface"], ["accent", "accent-soft"], ["accent-fg", "accent"],
  ["good", "surface"], ["good", "good-soft"],
  ["warning", "surface"], ["warning", "warning-soft"],
  ["critical", "surface"], ["critical", "critical-soft"],
];

describe.each(Object.entries(themes))("%s theme", (_name, t) => {
  it.each(TEXT)("%s on %s is at least 4.5:1", (fg, bg) => {
    expect(t[fg], fg).toBeDefined();
    expect(ratio(t[fg], t[bg])).toBeGreaterThanOrEqual(4.5);
  });

  it("form control borders are at least 3:1 against the surface", () => {
    expect(ratio(t["input-border"], t.surface)).toBeGreaterThanOrEqual(3);
  });
});
