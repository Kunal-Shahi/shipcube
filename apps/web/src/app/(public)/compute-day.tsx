"use client";

import { RotateCcw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/cn";

// Where each kind of work lands (packages/placement), and the hours of one day it's billed for.
// The hours are a picture of each billing model, not a measurement.
const WORK = [
  { work: "A report that changes once a day", where: "Static snapshot, refreshed on a schedule", hours: [5] },
  { work: "A dashboard your team opens", where: "Serverless, scales to zero between visits", hours: [9, 10, 13, 14, 15, 17] },
  { work: "A notebook that runs nightly", where: "Cloud Run job, billed while it runs", hours: [2, 3] },
  { work: "A model that needs a GPU", where: "NVIDIA L4, billed while it works", hours: [11, 12, 13] },
  { work: "An API that can't wait", where: "Always on, for steady latency", hours: Array.from({ length: 24 }, (_, h) => h) },
];
const TOTAL = WORK.reduce((n, w) => n + w.hours.length, 0);
const ALWAYS_ON = WORK.length * 24;
const DAY_MS = 7000;

/**
 * One day, played once when it scrolls into view: a playhead crosses 24 hours and each workload's
 * billed hours light up as it passes, with a running count against five servers left on all day.
 * Reduced motion: the finished day.
 */
export function ComputeDay() {
  const root = useRef<HTMLDivElement>(null);
  const [hour, setHour] = useState(0); // 0..24, where the playhead is
  const [play, setPlay] = useState(0); // bumped to replay
  const started = useRef(false);

  useEffect(() => {
    const el = root.current;
    if (!el) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setHour(24);
      return;
    }
    let raf = 0;
    const run = () => {
      const t0 = performance.now();
      const tick = (now: number) => {
        const h = Math.min(24, ((now - t0) / DAY_MS) * 24);
        setHour(h);
        if (h < 24) raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);
    };
    if (play > 0) {
      run();
      return () => cancelAnimationFrame(raf);
    }
    const io = new IntersectionObserver(
      ([e]) => {
        if (e.isIntersecting && !started.current) {
          started.current = true;
          run();
        }
      },
      { threshold: 0.4 },
    );
    io.observe(el);
    return () => {
      io.disconnect();
      cancelAnimationFrame(raf);
    };
  }, [play]);

  const billed = WORK.reduce((n, w) => n + w.hours.filter((h) => h < hour).length, 0);
  const server = Math.floor(hour) * WORK.length;
  const clock = `${String(Math.min(23, Math.floor(hour))).padStart(2, "0")}:00`;

  return (
    <div ref={root}>
      <div className="grid gap-x-10 gap-y-2 md:grid-cols-[minmax(0,330px)_minmax(0,1fr)] md:items-center">
        {/* The hour axis, over the cells. */}
        <div className="hidden md:block" />
        <div className="relative hidden h-5 text-[12px] text-fg-subtle md:block" aria-hidden>
          {[0, 6, 12, 18, 24].map((h) => (
            <span key={h} className="absolute -translate-x-1/2 first:translate-x-0 last:-translate-x-full" style={{ left: `${(h / 24) * 100}%` }}>
              {String(h).padStart(2, "0")}:00
            </span>
          ))}
        </div>

        {WORK.map((w) => (
          <Row key={w.work} work={w.work} where={w.where} hours={w.hours} hour={hour} />
        ))}
      </div>

      <div className="mt-10 grid gap-8 border-t border-border pt-8 md:grid-cols-[minmax(0,330px)_minmax(0,1fr)] md:items-end md:gap-x-10">
        <div className="flex items-center gap-3 text-[13px] text-fg-subtle">
          <span className="font-mono text-[15px] text-fg" aria-hidden>
            {hour >= 24 ? "24:00" : clock}
          </span>
          <button
            type="button"
            onClick={() => setPlay((p) => p + 1)}
            className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 hover:text-fg"
          >
            <RotateCcw className="size-3" aria-hidden /> Replay the day
          </button>
        </div>
        <div className="grid grid-cols-2 gap-8" aria-live="off">
          <div>
            <p className="font-display text-[56px] leading-none font-extrabold tracking-[-0.03em] text-fg tabular-nums">
              {billed}
            </p>
            <p className="mt-2 text-[14px] text-fg-muted">hours billed with ShipCube</p>
          </div>
          <div>
            <p className="font-display text-[56px] leading-none font-extrabold tracking-[-0.03em] text-fg-subtle tabular-nums">
              {server}
            </p>
            <p className="mt-2 text-[14px] text-fg-muted">if each ran on a server all day</p>
          </div>
        </div>
      </div>
      <p className="sr-only">
        Across these five workloads, {TOTAL} billed hours in a day, against {ALWAYS_ON} if each ran on its own server all day.
      </p>
      <p className="mt-6 text-[12px] text-fg-subtle">An illustration of each billing model, not a measurement.</p>
    </div>
  );
}

function Row({ work, where, hours, hour }: { work: string; where: string; hours: number[]; hour: number }) {
  return (
    <>
      <div className="pt-4 md:py-2.5">
        <p className="font-display text-[17px] font-semibold">{work}</p>
        <p className="mt-0.5 text-[14px] text-fg-muted">{where}</p>
      </div>
      <div className="relative" role="img" aria-label={`Billed for ${hours.length} of 24 hours`}>
        <div className="grid grid-cols-24 gap-[3px]">
          {Array.from({ length: 24 }, (_, h) => {
            const billed = hours.includes(h);
            const passed = h < hour;
            return (
              <span
                key={h}
                className={cn(
                  "h-7 rounded-[2px] transition-colors duration-200",
                  billed && passed ? "bg-mark" : billed ? "bg-mark/25" : "bg-surface-2",
                )}
              />
            );
          })}
        </div>
        {/* The playhead. */}
        {hour > 0 && hour < 24 && (
          <span
            className="pointer-events-none absolute -top-2 -bottom-2 w-0.5 bg-fg"
            style={{ left: `${(hour / 24) * 100}%` }}
            aria-hidden
          />
        )}
      </div>
    </>
  );
}
