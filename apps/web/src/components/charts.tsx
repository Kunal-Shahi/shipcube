"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

// Two small hand-built SVG charts (see the Charts section of docs/design/DESIGN.md).
// Both measure their container so text never scales, and both have a hover layer.

const H = 180;
const M = { top: 12, right: 12, bottom: 24, left: 52 };

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.floor(e.contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

function niceMax(v: number) {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
}

function Tooltip({ x, width, children }: { x: number; width: number; children: ReactNode }) {
  const left = Math.min(Math.max(x + 12, 0), width - 168);
  return (
    <div
      className="pointer-events-none absolute top-1 z-10 w-[156px] rounded-md border border-border bg-surface px-2.5 py-2 text-[12px] shadow-pop"
      style={{ left }}
    >
      {children}
    </div>
  );
}

function Grid({ yMax, innerW, innerH, fmt }: { yMax: number; innerW: number; innerH: number; fmt: (n: number) => string }) {
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * yMax);
  return (
    <g>
      {ticks.map((t) => {
        const y = M.top + innerH - (t / yMax) * innerH;
        return (
          <g key={t}>
            <line x1={M.left} x2={M.left + innerW} y1={y} y2={y} stroke="var(--border)" strokeWidth={1} />
            <text x={M.left - 8} y={y} dy="0.32em" textAnchor="end" fontSize={11} fill="var(--fg-subtle)" className="num">
              {fmt(t)}
            </text>
          </g>
        );
      })}
    </g>
  );
}

const hourFmt = (iso: string) =>
  new Date(iso).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "UTC" });
const dayFmt = (iso: string) => new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });

export function LatencyChart({ data }: { data: { t: string; p50: number; p95: number }[] }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const innerW = Math.max(width - M.left - M.right, 0);
  const innerH = H - M.top - M.bottom;
  const yMax = niceMax(Math.max(...data.map((d) => d.p95)));
  const x = (i: number) => M.left + (i / (data.length - 1)) * innerW;
  const y = (v: number) => M.top + innerH - (v / yMax) * innerH;
  const path = (k: "p50" | "p95") => data.map((d, i) => `${i ? "L" : "M"}${x(i)},${y(d[k])}`).join("");
  const area = `${path("p95")}L${x(data.length - 1)},${y(0)}L${x(0)},${y(0)}Z`;
  const last = data.length - 1;

  return (
    <figure>
      <div className="mb-2 flex flex-wrap items-center gap-4 text-[12px] text-fg-muted">
        <span className="flex items-center gap-1.5">
          <span className="h-0.5 w-3 rounded bg-accent" aria-hidden /> p95
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-0.5 w-3 rounded bg-fg-subtle" aria-hidden /> p50
        </span>
      </div>
      <div ref={ref} className="relative" style={{ height: H }}>
        {width === 0 && <div className="skeleton h-full w-full" aria-hidden />}
        {width > 0 && (
          <svg
            width={width}
            height={H}
            role="img"
            aria-label={`p95 latency over 24 hours, latest ${data[last].p95} ms; p50 latest ${data[last].p50} ms`}
            onMouseLeave={() => setHover(null)}
            onMouseMove={(e) => {
              const rect = e.currentTarget.getBoundingClientRect();
              const i = Math.round(((e.clientX - rect.left - M.left) / innerW) * last);
              setHover(i >= 0 && i <= last ? i : null);
            }}
          >
            <Grid yMax={yMax} innerW={innerW} innerH={innerH} fmt={(n) => (yMax >= 1000 ? `${(n / 1000).toFixed(1)} s` : `${Math.round(n)} ms`)} />
            {[0, 6, 12, 18, last].map((i) => (
              <text key={i} x={x(i)} y={H - 6} fontSize={11} fill="var(--fg-subtle)" textAnchor={i === 0 ? "start" : i === last ? "end" : "middle"} className="num">
                {hourFmt(data[i].t)}
              </text>
            ))}
            <path d={area} fill="var(--mark, var(--accent))" opacity={0.08} />
            <path d={path("p50")} fill="none" stroke="var(--fg-subtle)" strokeWidth={2} strokeLinejoin="round" />
            <path d={path("p95")} fill="none" stroke="var(--mark, var(--accent))" strokeWidth={2} strokeLinejoin="round" />
            <circle cx={x(last)} cy={y(data[last].p95)} r={4} fill="var(--mark, var(--accent))" stroke="var(--surface)" strokeWidth={2} />
            {hover !== null && (
              <g>
                <line x1={x(hover)} x2={x(hover)} y1={M.top} y2={M.top + innerH} stroke="var(--border-strong)" strokeWidth={1} />
                <circle cx={x(hover)} cy={y(data[hover].p95)} r={4} fill="var(--mark, var(--accent))" stroke="var(--surface)" strokeWidth={2} />
                <circle cx={x(hover)} cy={y(data[hover].p50)} r={4} fill="var(--fg-subtle)" stroke="var(--surface)" strokeWidth={2} />
              </g>
            )}
          </svg>
        )}
        {hover !== null && (
          <Tooltip x={x(hover)} width={width}>
            <div className="mb-1 text-fg-muted">{hourFmt(data[hover].t)} UTC</div>
            <div className="num flex justify-between"><span>p95</span><span className="font-medium">{data[hover].p95} ms</span></div>
            <div className="num flex justify-between"><span>p50</span><span className="font-medium">{data[hover].p50} ms</span></div>
          </Tooltip>
        )}
      </div>
      <div className="sr-only">
        <table>
          <caption>p50 and p95 latency by hour, UTC</caption>
          <thead>
            <tr><th scope="col">Hour</th><th scope="col">p50 (ms)</th><th scope="col">p95 (ms)</th></tr>
          </thead>
          <tbody>
            {data.map((d) => (
              <tr key={d.t}><th scope="row">{hourFmt(d.t)}</th><td>{d.p50}</td><td>{d.p95}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}

export function CostChart({ data }: { data: { t: string; value: number }[] }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const innerW = Math.max(width - M.left - M.right, 0);
  const innerH = H - M.top - M.bottom;
  const yMax = niceMax(Math.max(...data.map((d) => d.value)));
  const band = innerW / data.length;
  const barW = Math.max(band - 2, 1);
  const y = (v: number) => M.top + innerH - (v / yMax) * innerH;
  const fmt = (n: number) => (yMax < 1 ? `$${n.toFixed(2)}` : `$${n.toFixed(n < 10 && yMax <= 5 ? 1 : 0)}`);
  const r = Math.min(4, barW / 2);

  return (
    <figure>
      <div ref={ref} className="relative" style={{ height: H }}>
        {width === 0 && <div className="skeleton h-full w-full" aria-hidden />}
        {width > 0 && (
          <svg width={width} height={H} role="img" aria-label="Daily cost this month" onMouseLeave={() => setHover(null)}>
            <Grid yMax={yMax} innerW={innerW} innerH={innerH} fmt={fmt} />
            {data.map((d, i) => {
              const bx = M.left + i * band + 1;
              const by = y(d.value);
              const h = M.top + innerH - by;
              const rr = Math.min(r, h);
              return (
                <g key={d.t} onMouseEnter={() => setHover(i)}>
                  <rect x={M.left + i * band} y={M.top} width={band} height={innerH} fill="transparent" />
                  <path
                    d={`M${bx},${by + h}V${by + rr}Q${bx},${by} ${bx + rr},${by}H${bx + barW - rr}Q${bx + barW},${by} ${bx + barW},${by + rr}V${by + h}Z`}
                    fill="var(--mark, var(--accent))"
                    opacity={hover === null || hover === i ? 1 : 0.45}
                  />
                </g>
              );
            })}
            {[0, Math.floor(data.length / 2), data.length - 1].map((i) => (
              <text key={i} x={M.left + i * band + band / 2} y={H - 6} fontSize={11} fill="var(--fg-subtle)" textAnchor={i === 0 ? "start" : i === data.length - 1 ? "end" : "middle"}>
                {dayFmt(data[i].t)}
              </text>
            ))}
          </svg>
        )}
        {hover !== null && (
          <Tooltip x={M.left + hover * band} width={width}>
            <div className="mb-1 text-fg-muted">{dayFmt(data[hover].t)}</div>
            <div className="num flex justify-between"><span>Cost</span><span className="font-medium">${data[hover].value.toFixed(2)}</span></div>
          </Tooltip>
        )}
      </div>
      <div className="sr-only">
        <table>
          <caption>Cost by day, US dollars</caption>
          <thead>
            <tr><th scope="col">Day</th><th scope="col">Cost</th></tr>
          </thead>
          <tbody>
            {data.map((d) => (
              <tr key={d.t}><th scope="row">{dayFmt(d.t)}</th><td>${d.value.toFixed(2)}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}
