"use client";

import { useEffect, useRef } from "react";

export type CubeMode = "idle" | "run" | "wait" | "done";

const N = 9; // points along each edge
const ACCENT = [123, 140, 255];
const APPROVE = [255, 181, 71];
const GOOD = [74, 222, 154];

/** Points on the six faces of a unit cube, each face an N×N grid, with the face's outward normal.
 *  Edge points belong to two faces, so edges draw twice and read a little brighter. */
function cubePoints() {
  const pts: { p: number[]; n: number[] }[] = [];
  const step = 2 / (N - 1);
  for (let axis = 0; axis < 3; axis++) {
    for (const side of [-1, 1]) {
      const n = [0, 0, 0];
      n[axis] = side;
      for (let i = 0; i < N; i++) {
        for (let j = 0; j < N; j++) {
          const p = [0, 0, 0];
          p[axis] = side;
          p[(axis + 1) % 3] = -1 + i * step;
          p[(axis + 2) % 3] = -1 + j * step;
          pts.push({ p, n });
        }
      }
    }
  }
  return pts;
}

/**
 * The hero's cube: a grid of points on a slowly turning cube. While a request runs, a band of
 * light sweeps through it; amber while it waits for a person, green once the app is live.
 * With reduced motion it draws one still frame per mode.
 */
export function CubeField({ mode, className }: { mode: CubeMode; className?: string }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const modeRef = useRef(mode);
  const redraw = useRef<() => void>(() => {});
  modeRef.current = mode;

  useEffect(() => {
    redraw.current();
  }, [mode]);

  useEffect(() => {
    const el = canvas.current;
    if (!el) return;
    const ctx = el.getContext("2d");
    if (!ctx) return;
    const pts = cubePoints();
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let raf = 0;
    let visible = true;
    let t = 0;
    let last = performance.now();
    let w = 0;
    let h = 0;

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = el.clientWidth;
      h = el.clientHeight;
      el.width = Math.round(w * dpr);
      el.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    const draw = () => {
      const m = modeRef.current;
      ctx.clearRect(0, 0, w, h);
      const ay = t * 0.25 + 0.6;
      const ax = 0.52 + Math.sin(t * 0.15) * 0.08; // tilted toward the viewer: the top face shows
      const [sy, cy, sx, cx] = [Math.sin(ay), Math.cos(ay), Math.sin(ax), Math.cos(ax)];
      const size = Math.min(w, h) * 0.24;
      const tint = m === "wait" ? APPROVE : m === "done" ? GOOD : ACCENT;
      // The sweep: a band moving bottom to top, faster while work is happening.
      const band = m === "idle" ? null : ((t * (m === "run" ? 0.9 : 0.5)) % 2.6) - 1.3;
      const rotate = ([x, y, z]: number[]) => {
        const x1 = x * cy + z * sy;
        const z1 = -x * sy + z * cy;
        return [x1, y * cx - z1 * sx, y * sx + z1 * cx];
      };
      const projected = pts.map(({ p, n }) => {
        const [x1, y1, z2] = rotate(p);
        const persp = 6 / (6 + z2);
        // A face turned away from the viewer stays faint, so the front three faces read as a cube.
        const facing = rotate(n)[2] < 0;
        return { sx: w / 2 + x1 * size * persp, sy: h / 2 + y1 * size * persp, z: z2, y: p[1], facing };
      });
      projected.sort((a, b) => b.z - a.z);
      for (const p of projected) {
        const depth = (1.8 - p.z) / 3.6; // 0 at the back, 1 at the front
        const lit = band === null ? 0 : Math.max(0, 1 - Math.abs(-p.y - band) * 2.2);
        const base = lit > 0.05 ? tint : ACCENT;
        const alpha = p.facing ? 0.3 + depth * 0.5 + lit * 0.4 : 0.07;
        const r = p.facing ? 1 + depth * 1.4 + lit * 1.4 : 0.9;
        ctx.fillStyle = `rgb(${base[0]} ${base[1]} ${base[2]} / ${Math.min(alpha, 1)})`;
        ctx.beginPath();
        ctx.arc(p.sx, p.sy, r, 0, Math.PI * 2);
        ctx.fill();
      }
    };

    const frame = (now: number) => {
      t += Math.min(now - last, 50) / 1000;
      last = now;
      draw();
      raf = visible ? requestAnimationFrame(frame) : 0;
    };

    resize();
    redraw.current = () => {
      if (still) {
        t = modeRef.current === "idle" ? 0 : 1.6;
        draw();
      }
    };
    if (still) draw();
    else raf = requestAnimationFrame(frame);

    const ro = new ResizeObserver(() => {
      resize();
      draw();
    });
    ro.observe(el);
    // Stop drawing while the hero is scrolled away.
    const io = new IntersectionObserver(([e]) => {
      visible = e.isIntersecting;
      if (visible && !still && !raf) {
        last = performance.now();
        raf = requestAnimationFrame(frame);
      }
    });
    io.observe(el);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      io.disconnect();
    };
  }, []);

  return <canvas ref={canvas} className={className} aria-hidden />;
}
