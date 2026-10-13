// ── Flow that moves (pure) ─────────────────────────────────────────────────────
// Visual-layers plan 1, A3. With the overlay on, an edge whose component has
// a non-zero flow at the selected time step carries a dash whose
// `stroke-dashoffset` advances in the flow direction. The speed is one of five
// buckets of |p| / rating — the same share the 3D view turns a rotor by
// (site3d/resultStyle.ts: rpm = rated × share, share = |p| / cap clamped to 1),
// quantised in fifths so the two layers animate in step — and the motion is
// pure CSS: one keyframe, one duration class per bucket, no per-frame React
// work. Width by flow is the optional second reading of the same share.
//
// No React; the canvas puts `flowAnimationCss()` in its one <style> block and
// gives each moving edge the class and the CSS variable this module names.

/** Below this |p| (MW) an edge is idle: no motion. */
export const FLOW_EPS = 1e-3
export const FLOW_SPEED_BUCKETS = 5
export type FlowBucket = 0 | 1 | 2 | 3 | 4 | 5

/** |p| / rating, clamped to [0, 1]; 0 without a usable rating. */
export function flowShare(p: number, rating: number | null | undefined): number {
  if (!Number.isFinite(p) || rating == null || !Number.isFinite(rating) || rating <= 0) return 0
  return Math.min(1, Math.abs(p) / rating)
}

/**
 * The speed bucket of a flow: 0 idle (|p| below FLOW_EPS, or not a number);
 * otherwise fifths of the share, clamped — 1 up to 20 % of rating, 5 above
 * 80 % (and anything over the rating). A flow with no rating moves at the
 * slowest speed: it is flowing, but how hard cannot be said.
 */
export function flowSpeedBucket(p: number, rating: number | null | undefined): FlowBucket {
  if (!Number.isFinite(p) || Math.abs(p) < FLOW_EPS) return 0
  if (rating == null || !Number.isFinite(rating) || rating <= 0) return 1
  const share = Math.abs(p) / rating
  return Math.max(1, Math.min(FLOW_SPEED_BUCKETS, Math.ceil(share * FLOW_SPEED_BUCKETS))) as FlowBucket
}

/** Seconds per dash cycle, by bucket (index 0 unused). */
export const FLOW_BUCKET_PERIOD_S: readonly number[] = [0, 2.4, 1.6, 1.1, 0.75, 0.5]

/** The moving dash: 6 px on, 10 px off; one cycle is their sum. */
export const FLOW_DASH = '6 10'
export const FLOW_CYCLE_PX = 16

/** The class every moving edge carries, plus its bucket's duration class. */
export const FLOW_CLASS = 'canvas-flow'
export const flowClass = (bucket: FlowBucket): string => (bucket > 0 ? `${FLOW_CLASS} flow-b${bucket}` : '')

/**
 * The per-edge CSS variable: the dash offset one cycle advances to. A
 * negative offset moves the dashes along the path (source → target), so a
 * flow in the drawing direction (+1) is negative and a reverse flow positive.
 */
export const FLOW_CYCLE_VAR = '--flow-cycle'
export const flowCycleValue = (direction: 1 | -1): string => `${-direction * FLOW_CYCLE_PX}px`
export const flowDirection = (p: number): 1 | -1 => (p < 0 ? -1 : 1)

/** Stroke width when width-by-flow is on: 1.5 px idle to 7.5 px at rating. */
export const flowWidth = (share: number): number => 1.5 + 6 * Math.min(1, Math.max(0, share))

/**
 * The style block: one keyframe, one duration class per bucket, and the
 * reduced-motion rule that stops the dash (the colour band and the arrow chip
 * are unaffected — they are not animations).
 */
export function flowAnimationCss(): string {
  const classes = FLOW_BUCKET_PERIOD_S.slice(1)
    .map((s, i) => `.flow-b${i + 1} { animation-duration: ${s}s; }`)
    .join('\n      ')
  return `
      @keyframes canvasFlow { from { stroke-dashoffset: 0; } to { stroke-dashoffset: var(${FLOW_CYCLE_VAR}, -${FLOW_CYCLE_PX}px); } }
      .${FLOW_CLASS} { animation-name: canvasFlow; animation-timing-function: linear; animation-iteration-count: infinite; }
      ${classes}
      @media (prefers-reduced-motion: reduce) { .${FLOW_CLASS} { animation: none; } }
    `
}
