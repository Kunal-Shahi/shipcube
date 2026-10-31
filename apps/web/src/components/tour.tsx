"use client";

import { X } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { cn } from "@/lib/cn";
import { Button } from "./ui";

// The first-run tour of the console: a welcome card, a few spotlighted stops, a closing card.
// Anyone can skip it (Skip, the close button or Esc). Finishing or skipping sets a flag in this
// browser's storage so it doesn't come back; "Take the tour" in the sidebar replays it.
//
// Why browser storage: behind Firebase Hosting only the `__session` cookie reaches the server, so a
// separate cookie wouldn't work in production, and the API has no per-user preferences yet. The flag
// is per browser, like the theme. If storage is unavailable, the tour shows once per page load.

export const TOUR_KEY = "sc-tour-done";
/** Opens the tour on the Overview page, whatever the flag says (the sidebar's "Take the tour"). */
export const TOUR_PARAM = "tour";

export type TourStep = {
  /** The `data-tour` value of the element to spotlight. None, or not on the page: a centred card. */
  target?: string;
  title: string;
  body: string;
};

export const STEPS: TourStep[] = [
  {
    title: "Welcome to ShipCube",
    body: "A one-minute tour of the console: where your apps are, what they cost, and what needs you. Skip it any time; you can replay it from the sidebar.",
  },
  {
    target: "nav-setup",
    title: "Connect your agent first",
    body: "Create a token and add ShipCube to Claude Code, Cursor, Muse or any MCP client. Your agent estimates, deploys and caps apps from there.",
  },
  {
    target: "overview-headline",
    title: "Your workspace at a glance",
    body: "How many apps are healthy, what you've spent this month, how much traffic they served, and how many requests are waiting for you.",
  },
  {
    target: "overview-health",
    title: "App health",
    body: "Every app's status, latency, errors and spend against its cap. Open an app for its logs, monitoring and who can use it.",
  },
  {
    target: "nav-approvals",
    title: "Approvals",
    body: "First deploys to your project, cap raises, sharing with everyone, and deletes wait here for a person. Your agent can't approve its own requests.",
  },
  {
    target: "overview-budgets",
    title: "Budgets and caps",
    body: "Each app has a monthly spend cap and a daily AI token limit. Raising a cap above your workspace default needs an approval.",
  },
  {
    target: "nav-data",
    title: "Analyse your data",
    body: "Connect your warehouse and your agent can pull your data and run analytics on it, like revenue by region last month. It uses your metric definitions, so the numbers match yours.",
  },
  {
    title: "You're all set",
    body: "Ask your agent to deploy something and it will show up on the Overview. You can replay this tour from “Take the tour” in the sidebar.",
  },
];

const PAD = 6; // space between the target and the spotlight's edge
const GAP = 12; // space between the spotlight and the card
const CARD_W = 340;

function readDone() {
  try {
    return localStorage.getItem(TOUR_KEY) === "1";
  } catch {
    return false;
  }
}

function writeDone() {
  try {
    localStorage.setItem(TOUR_KEY, "1");
  } catch {}
}

/** The target's box, if it's on the page and visible. */
function measure(target?: string): DOMRect | null {
  if (!target) return null;
  const el = document.querySelector<HTMLElement>(`[data-tour="${target}"]`);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0 ? r : null;
}

/** Where the card goes: below the target if it fits, else above, else beside; clamped to the window. */
export function placeCard(rect: DOMRect | null, card: { w: number; h: number }, view: { w: number; h: number }) {
  if (!rect) return { left: Math.max((view.w - card.w) / 2, 16), top: Math.max((view.h - card.h) / 2, 16) };
  const clampX = (x: number) => Math.min(Math.max(x, 16), view.w - card.w - 16);
  const clampY = (y: number) => Math.min(Math.max(y, 16), view.h - card.h - 16);
  const below = rect.bottom + PAD + GAP;
  if (below + card.h <= view.h - 16) return { left: clampX(rect.left), top: below };
  const above = rect.top - PAD - GAP - card.h;
  if (above >= 16) return { left: clampX(rect.left), top: above };
  const right = rect.right + PAD + GAP;
  if (right + card.w <= view.w - 16) return { left: right, top: clampY(rect.top) };
  return { left: clampX(rect.left), top: clampY(view.h - card.h - 16) };
}

export function Tour() {
  const pathname = usePathname();
  const params = useSearchParams();
  const router = useRouter();
  const [step, setStep] = useState<number | null>(null);
  const [rect, setRect] = useState<DOMRect | null>(null);
  const [cardH, setCardH] = useState(200);
  const card = useRef<HTMLDivElement>(null);
  const primary = useRef<HTMLButtonElement>(null);
  const opener = useRef<Element | null>(null);

  // Start on the Overview page: on the first visit, or when asked to (?tour=1).
  useEffect(() => {
    if (pathname !== "/overview") return;
    const asked = params.get(TOUR_PARAM) === "1";
    if (asked || !readDone()) {
      opener.current = document.activeElement;
      setStep(0);
    }
    if (asked) router.replace("/overview", { scroll: false });
  }, [pathname, params, router]);

  const close = useCallback(() => {
    writeDone();
    setStep(null);
    setRect(null);
    // Give focus back to where it was, or to the main content.
    const back = opener.current as HTMLElement | null;
    (back && document.contains(back) ? back : document.getElementById("main"))?.focus?.();
  }, []);

  const current = step === null ? null : STEPS[step];

  // Bring the target into view, then measure it; keep it measured while the page scrolls or resizes.
  useLayoutEffect(() => {
    if (!current) return;
    const el = current.target ? document.querySelector<HTMLElement>(`[data-tour="${current.target}"]`) : null;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    // Scroll the target near the top of the window, so the card fits underneath it (a target taller
    // than the window shows its top part). The sidebar is pinned, so its items never need scrolling.
    if (el && !el.closest("aside")) {
      // On narrow screens the sidebar is a bar pinned across the top: keep the target below it.
      const bar = document.querySelector("aside");
      const below = bar && bar.getBoundingClientRect().width >= window.innerWidth - 1 ? bar.getBoundingClientRect().height : 0;
      window.scrollBy({ top: el.getBoundingClientRect().top - below - 24, behavior: still ? "auto" : "smooth" });
    }
    const update = () => setRect(measure(current.target));
    update();
    const t = setTimeout(update, still ? 0 : 400); // after a smooth scroll settles
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      clearTimeout(t);
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [current]);

  useLayoutEffect(() => {
    if (card.current) setCardH(card.current.offsetHeight);
    primary.current?.focus();
  }, [step, rect]);

  // Esc skips; arrows step; Tab stays inside the card.
  useEffect(() => {
    if (step === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
      else if (e.key === "ArrowRight" && step < STEPS.length - 1) setStep(step + 1);
      else if (e.key === "ArrowLeft" && step > 0) setStep(step - 1);
      else if (e.key === "Tab" && card.current) {
        const f = card.current.querySelectorAll<HTMLElement>("button");
        if (!f.length) return;
        const first = f[0];
        const last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [step, close]);

  if (step === null || !current) return null;
  const last = step === STEPS.length - 1;
  const first = step === 0;
  const view = typeof window === "undefined" ? { w: 1024, h: 768 } : { w: window.innerWidth, h: window.innerHeight };
  const w = Math.min(CARD_W, view.w - 32);
  const pos = placeCard(rect, { w, h: cardH }, view);
  // A target too tall to share the window with the card: spotlight only the part above the card.
  const spot = rect && pos.top > rect.top ? { ...rect.toJSON(), height: Math.min(rect.height, pos.top - GAP - PAD * 2 - rect.top) } : rect;
  // Stops between the welcome and closing cards are numbered.
  const stops = STEPS.length - 2;

  return (
    <div className="fixed inset-0 z-50" aria-hidden={false}>
      {/* Blocks the page while the tour is open; clicking it does nothing, so a stray click can't lose your place. */}
      <div className="absolute inset-0" />
      {spot ? (
        <div
          className="tour-spotlight pointer-events-none absolute rounded-lg"
          style={{ left: spot.left - PAD, top: spot.top - PAD, width: spot.width + PAD * 2, height: spot.height + PAD * 2 }}
          aria-hidden
        />
      ) : (
        <div className="tour-dim pointer-events-none absolute inset-0" aria-hidden />
      )}
      <div
        ref={card}
        role="dialog"
        aria-modal="true"
        aria-labelledby="tour-title"
        aria-describedby="tour-body"
        className="tour-card absolute rounded-xl border border-border bg-surface p-5 shadow-[var(--shadow)]"
        style={{ left: pos.left, top: pos.top, width: w }}
      >
        <div className="flex items-start justify-between gap-3">
          <p className="text-[12px] text-fg-muted" aria-live="polite">
            {first || last ? "Tour" : `Step ${step} of ${stops}`}
          </p>
          <button
            type="button"
            onClick={close}
            className="-mt-1 -mr-1 grid size-7 place-items-center rounded-md text-fg-subtle hover:bg-surface-2 hover:text-fg"
            aria-label="Close the tour"
          >
            <X className="size-4" aria-hidden />
          </button>
        </div>
        <h2 id="tour-title" className="mt-1 font-display text-[20px] leading-6 font-semibold">
          {current.title}
        </h2>
        <p id="tour-body" className="mt-2 text-[14px] leading-[22px] text-fg-muted">
          {current.body}
        </p>
        {!first && !last && (
          <div className="mt-4 flex gap-1" aria-hidden>
            {Array.from({ length: stops }, (_, i) => (
              <span key={i} className={cn("h-1 flex-1 rounded-full", i < step ? "bg-accent" : "bg-surface-2")} />
            ))}
          </div>
        )}
        <div className="mt-5 flex items-center justify-between gap-2">
          {first ? (
            <button type="button" onClick={close} className="text-[13px] text-fg-muted hover:text-fg hover:underline">
              Skip the tour
            </button>
          ) : last ? (
            <span />
          ) : (
            <Button size="sm" variant="ghost" onClick={() => setStep(step - 1)}>
              Back
            </Button>
          )}
          <div className="flex items-center gap-2">
            {!first && !last && (
              <button type="button" onClick={close} className="text-[13px] text-fg-muted hover:text-fg hover:underline">
                Skip
              </button>
            )}
            <Button
              ref={primary}
              variant="primary"
              className="h-9 px-4 font-semibold"
              onClick={() => (last ? close() : setStep(step + 1))}
            >
              {first ? "Start the tour" : last ? "Done" : "Next"}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
