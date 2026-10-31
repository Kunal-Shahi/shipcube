"use client";

import { useEffect, useRef } from "react";

export type CubeMode = "idle" | "run" | "wait" | "done";

const N = 9; // points along each edge

/** A token's colour as [r, g, b], from the page's CSS (see .landing in globals.css). */
function rgbOf(el: Element, name: string): number[] {
  const hex = getComputedStyle(el).getPropertyValue(name).trim().replace("#", "");
  const n = parseInt(hex.length === 3 ? hex.replace(/./g, "$&$&") : hex, 16);
  return Number.isNaN(n) ? [128, 128, 128] : [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

const mix = (a: number[], b: number[], k: number) => a.map((v, i) => Math.round(v + (b[i] - v) * k));
const easeOut = (x: number) => 1 - (1 - Math.min(Math.max(x, 0), 1)) ** 3;

type Point = { p: number[]; n: number[]; scatter: number[]; delay: number; seed: number };

/** Points on the six faces of a unit cube, each face an N×N grid, with the face's outward normal. */
function cubePoints(): Point[] {
  const pts: Point[] = [];
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
          // Where the point starts when the cube is built: an exploded view, each face pushed out
          // along its own direction, with a little jitter.
          const r = () => Math.random() * 2 - 1;
          const scatter = p.map((v, k) => v + n[k] * 0.55 + r() * 0.08);
          // Built from the bottom up (canvas y points down, so +1 is the bottom face).
          const delay = ((1 - p[1]) / 2) * 0.7 + Math.random() * 0.12;
          pts.push({ p, n, scatter, delay, seed: Math.random() * Math.PI * 2 });
        }
      }
    }
  }
  return pts;
}

/**
 * The hero's cube, a grid of points on a turning cube, and what the agent is doing:
 * - idle: turns slowly and breathes;
 * - run: its faces slide in from an exploded view, bottom first, then a band of light climbs it;
 * - wait: slows almost to a stop and pulses amber, holding for a person;
 * - done: a green ripple runs out from the middle, and it settles back to turning.
 * `runKey` changes with each new request, which restarts the build. Every phase is timed from
 * its own start, so switching requests never jumps mid-animation. Reduced motion: one still frame.
 */
export function CubeField({ mode, runKey, className }: { mode: CubeMode; runKey: string; className?: string }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const state = useRef({ mode, runKey, modeAt: 0, buildAt: 0, now: 0 });
  const redraw = useRef<() => void>(() => {});

  // Phase changes are stamped with the animation's own clock.
  useEffect(() => {
    const s = state.current;
    if (s.runKey !== runKey) {
      s.runKey = runKey;
      s.buildAt = s.now;
    }
    if (s.mode !== mode) {
      s.mode = mode;
      s.modeAt = s.now;
    }
    redraw.current();
  }, [mode, runKey]);

  useEffect(() => {
    const el = canvas.current;
    if (!el) return;
    const ctx = el.getContext("2d");
    if (!ctx) return;
    const pts = cubePoints();
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const s = state.current;
    let raf = 0;
    let visible = true;
    let last = performance.now();
    let angle = 0.6;
    let speed = 0.25;
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

    const draw = (dt: number) => {
      const t = s.now;
      const m = s.mode;
      const since = t - s.modeAt;
      const [accent, approve, good] = ["--cube", "--approve", "--good"].map((v) => rgbOf(el, v));

      // Turning speed eases toward each mode's own pace.
      const target = m === "run" ? 0.7 : m === "wait" ? 0.04 : m === "done" ? 0.35 : 0.22;
      speed += (target - speed) * Math.min(1, dt * 2.5);
      angle += speed * dt;
      const ax = 0.52 + Math.sin(t * 0.3) * 0.06;
      const [sy, cy, sx, cx] = [Math.sin(angle), Math.cos(angle), Math.sin(ax), Math.cos(ax)];
      const rotate = ([x, y, z]: number[]) => {
        const x1 = x * cy + z * sy;
        const z1 = -x * sy + z * cy;
        return [x1, y * cx - z1 * sx, y * sx + z1 * cx];
      };
      const size = Math.min(w, h) * 0.24;
      const build = t - s.buildAt;
      const pulse = m === "wait" ? Math.sin(since * 5) ** 2 : 0;
      const band = m === "run" ? ((since * 0.8) % 2.8) - 1.4 : null;
      const ripple = m === "done" ? since * 1.6 : null;

      ctx.clearRect(0, 0, w, h);
      const drawn = pts.map((pt) => {
        // While building, each point flies home from its scattered start.
        const k = easeOut((build - pt.delay) / 0.75);
        const home = pt.p.map((v, j) => pt.scatter[j] + (v - pt.scatter[j]) * k);
        // Idle and done: a gentle breath outward along the face. Waiting: a heartbeat.
        const breath = m === "idle" || m === "done" ? Math.sin(t * 1.4 + pt.seed) * 0.012 : 0;
        const pos = home.map((v, j) => v + pt.n[j] * (breath + pulse * 0.06));
        const [x1, y1, z2] = rotate(pos);
        const persp = 6 / (6 + z2);
        return {
          sx: w / 2 + x1 * size * persp,
          sy: h / 2 + y1 * size * persp,
          z: z2,
          y: pt.p[1],
          facing: rotate(pt.n)[2] < 0,
          dist: Math.hypot(x1, y1) / 1.6,
          built: k,
        };
      });
      drawn.sort((a, b) => b.z - a.z);
      for (const d of drawn) {
        const depth = (1.8 - d.z) / 3.6;
        let color = accent;
        let lit = 0;
        if (band !== null) {
          lit = Math.max(0, 1 - Math.abs(-d.y - band) * 2.4);
          color = mix(accent, mix(accent, [255, 255, 250], 0.6), lit);
        }
        if (m === "wait") {
          lit = 0.4 + pulse * 0.6;
          color = mix(accent, approve, Math.min(1, since * 2));
        }
        if (ripple !== null) {
          const wave = Math.max(0, 1 - Math.abs(d.dist - ripple) * 5);
          const fade = Math.max(0, 1 - since / 2.2);
          lit = wave;
          color = mix(accent, good, Math.max(wave, fade * 0.6));
        }
        const base = d.facing ? 0.28 + depth * 0.5 : 0.06;
        const alpha = Math.min(1, (base + lit * 0.4) * (0.35 + d.built * 0.65));
        const r = d.facing ? 1 + depth * 1.4 + lit * 1.6 : 0.9;
        ctx.fillStyle = `rgb(${color[0]} ${color[1]} ${color[2]} / ${alpha})`;
        ctx.beginPath();
        ctx.arc(d.sx, d.sy, r, 0, Math.PI * 2);
        ctx.fill();
      }
    };

    const frame = (now: number) => {
      const dt = Math.min(now - last, 50) / 1000;
      last = now;
      s.now += dt;
      draw(dt);
      raf = visible ? requestAnimationFrame(frame) : 0;
    };

    resize();
    // Reduced motion: a settled cube in the current mode's colour, redrawn when the mode changes.
    redraw.current = () => {
      if (!still) return;
      s.buildAt = -10;
      s.modeAt = s.now - 0.6;
      draw(0);
    };
    if (still) redraw.current();
    else raf = requestAnimationFrame(frame);

    const ro = new ResizeObserver(() => {
      resize();
      draw(0);
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
