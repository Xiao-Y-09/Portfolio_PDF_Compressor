"use client";

/**
 * Home page background, fixed full-viewport: a field of small, semi-transparent isometric
 * building volumes standing on a dashed ground grid, based on the finalized axon-bg design.
 * Every block sits exactly on grid cells and keeps a fixed on-screen scale (the window only
 * changes how many there are); which lots are filled, and each block's footprint and height,
 * are re-rolled on every page load. Blocks draw in as a wave from the top-right corner in nine
 * .c0–.c8 bands, then each one bobs around its grid position on its own phase (.floatA),
 * and a moving pointer knocks nearby blocks aside before they spring back. The ground grid
 * never moves. Decorative only; styles live in globals.css.
 */

import { useEffect, useRef, useState, type CSSProperties } from "react";

// Design units → CSS px. The original cluster rendered at ~2.85 px/unit on a 1920×1080
// window; 1.4 keeps every block at about half that size on any screen.
const SCALE = 1.4;
const GRID_U = 130 / 3; // ground grid pitch along each isometric axis
const GRID_V = 40;
const LOT = 3; // one block (or none) per 3×3 grid cells
const EMPTY_LOTS = 0.35; // share of lots left empty, so the field isn't wall-to-wall
// Random block size per load: 1–2 grid cells along each axis, height within the range of
// the original nine volumes (25–95 design units), in steps of 5.
const MAX_CELLS = 2;
const MIN_HEIGHT = 25;
const MAX_HEIGHT = 95;
const BLOCK_COLOR = "#3f3f46"; // zinc-700
const BLOCK_EDGE_OPACITY = 0.5;
// Face tint per visible face, lighter on top so the translucent volumes read as 3D.
const FACE_OPACITY = { top: 0.03, left: 0.06, right: 0.09 };
const GRID_COLOR = "#a1a1aa"; // zinc-400, a step lighter than the blocks
const BANDS = 9; // draw-in bands, matching .c0–.c8
const BAND_DELAY_S = 0.35; // same stagger as the .c0–.c8 delays in globals.css
const FLOAT_PERIOD_S = 11; // matches the .floatA duration in globals.css
const FLOAT_AMPLITUDE_PX = 6; // ± around the grid position (12px travel), ×0.9–1.1 per block
// Pointer bump: blocks within HIT_RADIUS px of a moving pointer get pushed away from it,
// harder the closer and faster it passes, then a damped spring pulls them back.
const HIT_RADIUS = 140;
const HIT_IMPULSE = 0.045;
const MAX_PUSH = 20; // px
const SPRING = 0.012; // per 60fps frame
const DAMPING = 0.1; // per 60fps frame; leaves one small overshoot on the way back
const BUCKET = 320; // regenerate only when the window grows past a 320px step
const SQRT3 = Math.sqrt(3);

type Block = {
  key: string;
  band: number;
  box: { x: number; y: number; w: number; h: number }; // design units, padded for the stroke
  cx: number; // volume center, design units
  cy: number;
  amp: number; // float amplitude, px
  phase: number; // float phase, s
  edges: string;
  top: string;
  left: string;
  right: string;
};
type Field = { width: number; height: number; viewBox: string; grid: string; blocks: Block[] };

// (p, q) are distances along the two isometric axes; the origin is the viewport's top-right corner.
const iso = (p: number, q: number): [number, number] => [(SQRT3 / 2) * (p - q), (p + q) / 2];
const pt = ([x, y]: [number, number]) => `${x.toFixed(1)},${y.toFixed(1)}`;

function hash(i: number, j: number, seed: number): number {
  let h = Math.imul(i, 73856093) ^ Math.imul(j, 19349663) ^ Math.imul(seed, 83492791);
  h = Math.imul(h ^ (h >>> 13), 0x5bd1e995);
  return (h ^ (h >>> 15)) >>> 0;
}

function buildField(width: number, height: number, visibleDiag: number, seed: number): Field {
  const w = width / SCALE;
  const h = height / SCALE;
  // (p, q) range covering the rect x ∈ [-w, 0], y ∈ [0, h], padded for the tallest block.
  const pad = LOT * GRID_U + 100;
  const pMin = -w / SQRT3 - pad;
  const pMax = h + pad;
  const qMin = -pad;
  const qMax = h + w / SQRT3 + pad;

  const lines: string[] = [];
  for (let k = Math.ceil(pMin / GRID_U); k * GRID_U <= pMax; k++) {
    lines.push(`M${pt(iso(k * GRID_U, qMin))} L${pt(iso(k * GRID_U, qMax))}`);
  }
  for (let k = Math.ceil(qMin / GRID_V); k * GRID_V <= qMax; k++) {
    lines.push(`M${pt(iso(pMin, k * GRID_V))} L${pt(iso(pMax, k * GRID_V))}`);
  }

  const blocks: Block[] = [];
  const diag = visibleDiag / SCALE;
  for (let i = Math.floor(pMin / (LOT * GRID_U)); i * LOT * GRID_U <= pMax; i++) {
    for (let j = Math.floor(qMin / (LOT * GRID_V)); j * LOT * GRID_V <= qMax; j++) {
      const r = hash(i, j, seed);
      if (((r >>> 16) % 100) / 100 < EMPTY_LOTS) continue;
      const cu = 1 + (r % MAX_CELLS);
      const cv = 1 + ((r >>> 2) % MAX_CELLS);
      const r2 = hash(j + 7919, i - 104729, seed); // independent bits for height and float
      const bh = MIN_HEIGHT + 5 * ((r2 >>> 20) % ((MAX_HEIGHT - MIN_HEIGHT) / 5 + 1));
      // Start on a grid intersection inside the lot, so every edge lands on a grid line.
      const p = (i * LOT + ((r >>> 8) % (LOT - cu + 1))) * GRID_U;
      const q = (j * LOT + ((r >>> 12) % (LOT - cv + 1))) * GRID_V;
      const a = cu * GRID_U;
      const b = cv * GRID_V;
      // base/roof corners run back → right → front → left
      const base = [iso(p, q), iso(p + a, q), iso(p + a, q + b), iso(p, q + b)];
      const left = base[3][0], right = base[1][0], top = base[0][1] - bh, bottom = base[2][1];
      if (right < -w || left > 0 || bottom < 0 || top > h) continue;

      const roof = base.map(([x, y]) => [x, y - bh] as [number, number]);
      // Same edge order as the original paths: base loop, roof loop, then the verticals.
      const edges = [
        ...base.map((c, k) => `M${pt(c)} L${pt(base[(k + 1) % 4])}`),
        ...roof.map((c, k) => `M${pt(c)} L${pt(roof[(k + 1) % 4])}`),
        ...base.map((c, k) => `M${pt(c)} L${pt(roof[k])}`),
      ];
      const face = (...cs: [number, number][]) => `M${cs.map(pt).join(" L")} Z`;
      const cx = (left + right) / 2;
      const cy = (top + bottom) / 2;
      blocks.push({
        key: `${i},${j}`,
        band: Math.min(BANDS - 1, Math.floor((BANDS * Math.hypot(cx, cy)) / diag)),
        box: { x: left - 1, y: top - 1, w: right - left + 2, h: bottom - top + 2 },
        cx,
        cy,
        amp: FLOAT_AMPLITUDE_PX * (0.9 + 0.2 * ((r2 % 1001) / 1000)),
        phase: FLOAT_PERIOD_S * (((r2 >>> 10) % 1000) / 1000),
        edges: edges.join(" "),
        top: face(...roof),
        right: face(base[1], base[2], roof[2], roof[1]),
        left: face(base[2], base[3], roof[3], roof[2]),
      });
    }
  }

  return { width, height, viewBox: `${-w} 0 ${w} ${h}`, grid: lines.join(" "), blocks };
}

export default function AxonBackground() {
  const [field, setField] = useState<Field | null>(null);
  const blockEls = useRef<(HTMLDivElement | null)[]>([]);

  useEffect(() => {
    // New layout on every page load; kept for the session so resizes only extend it.
    const seed = Math.floor(Math.random() * 0x7fffffff);
    let current = "";
    const update = () => {
      const vw = document.documentElement.clientWidth;
      const vh = document.documentElement.clientHeight;
      const bw = Math.ceil(vw / BUCKET) * BUCKET;
      const bh = Math.ceil((vh + 2 * MAX_PUSH) / BUCKET) * BUCKET;
      if (`${bw}x${bh}` === current) return;
      current = `${bw}x${bh}`;
      setField(buildField(bw, bh, Math.hypot(vw, vh), seed));
    };
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);

  // Pointer bump: push blocks near a moving pointer, then spring them back to rest.
  useEffect(() => {
    if (!field || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const blocks = field.blocks;
    const state = blocks.map(() => ({ x: 0, y: 0, vx: 0, vy: 0 }));
    let last: { x: number; y: number } | null = null;
    let raf = 0;
    let prevTime = 0;

    const step = (now: number) => {
      const dt = prevTime ? Math.min((now - prevTime) / (1000 / 60), 3) : 1;
      prevTime = now;
      let moving = false;
      state.forEach((s, k) => {
        if (!s.x && !s.y && !s.vx && !s.vy) return;
        const keep = Math.pow(1 - DAMPING, dt);
        s.vx = (s.vx - SPRING * s.x * dt) * keep;
        s.vy = (s.vy - SPRING * s.y * dt) * keep;
        s.x += s.vx * dt;
        s.y += s.vy * dt;
        const d = Math.hypot(s.x, s.y);
        if (d > MAX_PUSH) {
          s.x *= MAX_PUSH / d;
          s.y *= MAX_PUSH / d;
        }
        if (d < 0.05 && Math.hypot(s.vx, s.vy) < 0.05) {
          s.x = s.y = s.vx = s.vy = 0;
        } else {
          moving = true;
        }
        const el = blockEls.current[k];
        if (el) el.style.transform = s.x || s.y ? `translate(${s.x}px, ${s.y}px)` : "";
      });
      raf = moving ? requestAnimationFrame(step) : 0;
      if (!raf) prevTime = 0;
    };

    const onMove = (e: PointerEvent) => {
      if (last) {
        const speed = Math.min(Math.hypot(e.clientX - last.x, e.clientY - last.y), 40);
        const vw = document.documentElement.clientWidth;
        blocks.forEach((b, k) => {
          // The block layer is pinned to the viewport's top-right corner.
          const ox = vw + b.cx * SCALE - e.clientX;
          const oy = b.cy * SCALE - e.clientY;
          const d = Math.hypot(ox, oy);
          if (d >= HIT_RADIUS || d === 0) return;
          const f = ((1 - d / HIT_RADIUS) * speed * HIT_IMPULSE) / d;
          state[k].vx += ox * f;
          state[k].vy += oy * f;
        });
        if (!raf) raf = requestAnimationFrame(step);
      }
      last = { x: e.clientX, y: e.clientY };
    };

    window.addEventListener("pointermove", onMove, { passive: true });
    return () => {
      window.removeEventListener("pointermove", onMove);
      cancelAnimationFrame(raf);
      blockEls.current.forEach((el) => el && (el.style.transform = ""));
    };
  }, [field]);

  return (
    <div aria-hidden="true" className="axon-bg">
      {field && (
        <>
          <svg className="axon-ground" width={field.width} height={field.height} viewBox={field.viewBox}>
            <path d={field.grid} fill="none" stroke={GRID_COLOR} strokeWidth="0.4" strokeDasharray="3 4" />
          </svg>
          <div className="axon-blocks" style={{ width: field.width, height: field.height }}>
            {field.blocks.map((b, k) => (
              <div
                key={b.key}
                ref={(el) => {
                  blockEls.current[k] = el;
                }}
                className="axon-block"
                style={{ left: field.width + b.box.x * SCALE, top: b.box.y * SCALE }}
              >
                <svg
                  className="floatA"
                  width={b.box.w * SCALE}
                  height={b.box.h * SCALE}
                  viewBox={`${b.box.x} ${b.box.y} ${b.box.w} ${b.box.h}`}
                  style={{ "--amp": `${b.amp}px`, animationDelay: `${-b.phase}s` } as CSSProperties}
                >
                  <g
                    className="axon-faces"
                    fill={BLOCK_COLOR}
                    style={{ animationDelay: `${b.band * BAND_DELAY_S}s` }}
                  >
                    <path d={b.top} fillOpacity={FACE_OPACITY.top} />
                    <path d={b.left} fillOpacity={FACE_OPACITY.left} />
                    <path d={b.right} fillOpacity={FACE_OPACITY.right} />
                  </g>
                  <path
                    className={`c${b.band}`}
                    d={b.edges}
                    fill="none"
                    stroke={BLOCK_COLOR}
                    strokeOpacity={BLOCK_EDGE_OPACITY}
                    strokeWidth="0.6"
                    strokeLinejoin="round"
                  />
                </svg>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
