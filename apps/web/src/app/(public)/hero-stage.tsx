"use client";

import { RotateCcw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/cn";
import { CubeField, type CubeMode } from "./cube-field";

type Step = { tool: string; text: string; tone?: "wait" | "ok" };
type Request = { team: string; ask: string; steps: Step[] };

// Illustrations of what an agent does with ShipCube, one per kind of work. The tools are the real
// MCP tools; the apps, people and times are made up.
const REQUESTS: Request[] = [
  {
    team: "Analyst",
    ask: "Run my churn notebook on the warehouse every night.",
    steps: [
      { tool: "estimate_load", text: "batch work, memory-heavy: a scheduled Cloud Run job, billed only while it runs" },
      { tool: "data_query", text: "reads orders and customers through governed measures, not raw SQL" },
      { tool: "deploy_app", text: "the first deploy to your GCP project waits for a person", tone: "wait" },
      { tool: "approved", text: "in the console, with one click", tone: "ok" },
      { tool: "deploy_app", text: "scheduled for 02:00 every night. Your laptop can sleep.", tone: "ok" },
    ],
  },
  {
    team: "Finance",
    ask: "Ship this revenue tracker and share it with finance.",
    steps: [
      { tool: "estimate_load", text: "interactive and bursty: serverless, scales to zero when nobody's using it" },
      { tool: "deploy_app", text: "live at a private URL, behind Google sign-in", tone: "ok" },
      { tool: "set_access", text: "finance-team@example.com can open it; nobody else can" },
      { tool: "set_caps", text: "spend capped per month, AI tokens capped per day" },
    ],
  },
  {
    team: "ML",
    ask: "Embed two million support tickets on a GPU.",
    steps: [
      { tool: "estimate_load", text: "GPU libraries found: an NVIDIA L4, billed only while it works" },
      { tool: "set_caps", text: "a cap above the workspace default waits for a person", tone: "wait" },
      { tool: "approved", text: "the cap is raised for this app only", tone: "ok" },
      { tool: "deploy_app", text: "running on an L4. The tickets never touched your laptop.", tone: "ok" },
    ],
  },
  {
    team: "Ops",
    ask: "Delete the dashboard nobody opens anymore.",
    steps: [
      { tool: "inspect_app", text: "no visits in the last 30 days, still costing a little" },
      { tool: "delete_app", text: "deletes always wait for a person", tone: "wait" },
      { tool: "approved", text: "the approval page said exactly what goes", tone: "ok" },
      { tool: "delete_app", text: "deleted, and restorable from the console", tone: "ok" },
    ],
  },
];

const STEP_MS = 900;
const WAIT_MS = 1800;
const NEXT_MS = 5000;

/** The hero: pick a request, watch the agent carry it out through ShipCube, and see the step a person approves. */
export function HeroStage() {
  const [active, setActive] = useState(0);
  const [shown, setShown] = useState(0);
  const [picked, setPicked] = useState(false);
  const [still, setStill] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const req = REQUESTS[active];

  useEffect(() => {
    setStill(window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }, []);

  // Reveal one step at a time; a step that waits for a person holds a little longer.
  useEffect(() => {
    clearTimeout(timer.current);
    if (still) {
      setShown(req.steps.length);
      return;
    }
    if (shown < req.steps.length) {
      const prev = req.steps[shown - 1];
      timer.current = setTimeout(() => setShown((s) => s + 1), prev?.tone === "wait" ? WAIT_MS : shown === 0 ? 600 : STEP_MS);
    } else if (!picked) {
      // Until someone picks one, the stage walks through every request in turn.
      timer.current = setTimeout(() => {
        setActive((a) => (a + 1) % REQUESTS.length);
        setShown(0);
      }, NEXT_MS);
    }
    return () => clearTimeout(timer.current);
  }, [shown, active, picked, still, req.steps]);

  const pick = (i: number) => {
    setPicked(true);
    setActive(i);
    setShown(0);
  };

  const last = req.steps[shown - 1];
  const done = shown >= req.steps.length;
  const mode: CubeMode = shown === 0 ? "idle" : done ? "done" : last?.tone === "wait" ? "wait" : "run";

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] lg:items-stretch">
      <div className="relative flex flex-col">
        <div className="relative h-[300px] sm:h-[400px] lg:my-auto lg:h-[520px]">
          <CubeField mode={mode} className="absolute inset-0 size-full" />
          <div
            className={cn(
              "pointer-events-none absolute bottom-0 left-1/2 -translate-x-1/2 rounded-full border px-3 py-1 text-[12px] whitespace-nowrap transition-colors",
              mode === "wait"
                ? "border-warning/40 bg-warning-soft text-warning"
                : mode === "done"
                  ? "border-good/40 bg-good-soft text-good"
                  : "border-border bg-surface/80 text-fg-muted",
            )}
            aria-hidden
          >
            {mode === "wait" ? "Waiting for a person" : mode === "done" ? "Done" : mode === "run" ? "Working" : "Ready"}
          </div>
        </div>
      </div>

      <div className="flex flex-col gap-3">
        <div role="group" aria-label="Pick a request to send to your agent" className="grid gap-2 sm:grid-cols-2">
          {REQUESTS.map((r, i) => (
            <button
              key={r.team}
              type="button"
              onClick={() => pick(i)}
              aria-pressed={i === active}
              className={cn(
                "rounded-lg border px-3 py-2.5 text-left transition-colors",
                i === active ? "border-accent bg-accent-soft" : "border-input-border bg-surface/70 hover:border-fg-muted",
              )}
            >
              <span className="block text-[12px] font-medium text-accent">{r.team}</span>
              <span className="mt-0.5 block text-[13.5px] leading-5 text-fg">{r.ask}</span>
            </button>
          ))}
        </div>

        <figure className="flex min-h-[300px] flex-1 flex-col overflow-hidden rounded-xl border border-border-strong bg-[#0a0f24]/90">
          <figcaption className="flex items-center justify-between gap-2 border-b border-border px-4 py-2.5 text-[12px] text-fg-subtle">
            <span>Muse / Claude Code, with ShipCube connected</span>
            <button
              type="button"
              onClick={() => pick(active)}
              className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 hover:text-fg"
              aria-label="Replay this example"
            >
              <RotateCcw className="size-3" aria-hidden /> Replay
            </button>
          </figcaption>
          <ol className="flex flex-col gap-2.5 p-4 font-mono text-[12.5px] leading-[20px] sm:p-5" aria-live="polite">
            <li className="font-sans text-[14px] leading-[22px] text-fg">
              <span className="mr-2 text-accent" aria-hidden>
                &gt;
              </span>
              {req.ask}
            </li>
            {req.steps.slice(0, shown).map((s, i) => (
              <li key={`${active}-${i}`} className="stage-line flex flex-wrap gap-x-2">
                <span className={s.tone === "wait" ? "text-[var(--approve)]" : s.tone === "ok" ? "text-good" : "text-accent"}>
                  {s.tool === "approved" ? "you approved" : `shipcube.${s.tool}`}
                </span>
                <span className="text-fg-muted">
                  {s.tone === "wait" && (
                    <span
                      className={cn("mr-1.5 inline-block size-2 rounded-full bg-[var(--approve)] align-middle", i === shown - 1 && !done && "stage-wait")}
                      aria-hidden
                    />
                  )}
                  {s.text}
                </span>
              </li>
            ))}
            {!done && (
              <li aria-hidden>
                <span className="text-accent">&gt;</span> <span className="term-caret inline-block h-4 w-2 translate-y-0.5 bg-fg-muted" />
              </li>
            )}
          </ol>
          <p className="mt-auto px-4 pb-3 text-[11.5px] text-fg-subtle sm:px-5">Illustrated example. The tools are real; the apps are not.</p>
        </figure>
      </div>
    </div>
  );
}
