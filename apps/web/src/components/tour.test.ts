import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ usePathname: vi.fn(), useRouter: vi.fn(), useSearchParams: vi.fn() }));

const { STEPS, placeCard } = await import("./tour");

const view = { w: 1280, h: 800 };
const card = { w: 340, h: 200 };
const box = (left: number, top: number, width: number, height: number) =>
  ({ left, top, width, height, right: left + width, bottom: top + height, x: left, y: top, toJSON: () => ({}) }) as DOMRect;

describe("placeCard", () => {
  it("centres the card when there's nothing to point at", () => {
    expect(placeCard(null, card, view)).toEqual({ left: 470, top: 300 });
  });

  it("puts the card below the target when it fits", () => {
    expect(placeCard(box(100, 100, 200, 40), card, view)).toEqual({ left: 100, top: 158 });
  });

  it("puts it above when there's no room below", () => {
    expect(placeCard(box(100, 600, 200, 100), card, view).top).toBe(600 - 6 - 12 - 200);
  });

  it("puts it beside a tall target, like the sidebar", () => {
    expect(placeCard(box(0, 0, 224, 800), card, view)).toEqual({ left: 224 + 6 + 12, top: 16 });
  });

  it("keeps the card inside the window", () => {
    const p = placeCard(box(1200, 100, 60, 30), card, view);
    expect(p.left + card.w).toBeLessThanOrEqual(view.w - 16);
  });
});

describe("STEPS", () => {
  it("opens with a welcome and closes with a summary, neither pointing at anything", () => {
    expect(STEPS[0].target).toBeUndefined();
    expect(STEPS.at(-1)?.target).toBeUndefined();
  });

  it("points every stop in between at a console element", () => {
    for (const s of STEPS.slice(1, -1)) expect(s.target).toMatch(/^(nav|overview)-/);
  });
});
