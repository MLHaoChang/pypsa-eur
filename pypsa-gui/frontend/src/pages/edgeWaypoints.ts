// ── Edge waypoint maths (pure) ────────────────────────────────────────────────
// What EditableEdge (TopologyCanvas) does to a route when the user inserts,
// drags or removes a waypoint, and how it draws and labels the polyline —
// without React Flow, so the rules are tested on arrays (visual-layers plan
// 1, A5). A route is the interior waypoints only; the bus ends are supplied
// by React Flow.
import type { WP } from './topologyLayoutStore'

/** Undo depth per edge; the oldest state falls off beyond it. `history[0]` is the straight-line state. */
export const HISTORY_CAP = 50

export const midpoint = (a: WP, b: WP): WP => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 })

/**
 * Insert a waypoint on segment `segIdx` of the drawn polyline (segment i runs
 * from point i to point i + 1, point 0 being the source). The new waypoint
 * lands at index `segIdx` of the route, between the points of that segment.
 */
export function insertWaypoint(wps: readonly WP[], segIdx: number, at: WP): WP[] {
  const i = Math.max(0, Math.min(wps.length, segIdx))
  return [...wps.slice(0, i), { x: at.x, y: at.y }, ...wps.slice(i)]
}

export const moveWaypoint = (wps: readonly WP[], idx: number, to: WP): WP[] =>
  wps.map((wp, i) => (i === idx ? { x: to.x, y: to.y } : wp))

export const removeWaypoint = (wps: readonly WP[], idx: number): WP[] =>
  wps.filter((_, i) => i !== idx)

/** The history after committing `wps`: a deep copy appended, the oldest dropped at the cap. */
export function pushHistory(history: readonly WP[][], wps: readonly WP[], cap = HISTORY_CAP): WP[][] {
  const next = history.map(h => h.map(p => ({ ...p })))
  if (next.length >= cap) next.shift()
  next.push(wps.map(p => ({ x: p.x, y: p.y })))
  return next
}

/** The SVG path of source → waypoints → target. */
export const buildPathD = (source: WP, wps: readonly WP[], target: WP): string =>
  [source, ...wps, target].map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ')

/** The index of the middle segment — where the badge sits. */
export const middleSegmentIndex = (pointCount: number): number => Math.floor((pointCount - 1) / 2)

/**
 * The point at exactly half the polyline's length, and the direction of the
 * segment it lies on — where the transformer symbol sits, so it stays between
 * the buses however the route bends.
 */
export function pathMidpoint(points: readonly WP[]): { x: number; y: number; angleRad: number } {
  if (points.length < 2) return { x: points[0]?.x ?? 0, y: points[0]?.y ?? 0, angleRad: 0 }
  const dist = (a: WP, b: WP) => Math.hypot(b.x - a.x, b.y - a.y)
  let total = 0
  for (let i = 0; i < points.length - 1; i++) total += dist(points[i], points[i + 1])
  if (total === 0) return { x: points[0].x, y: points[0].y, angleRad: 0 }
  const half = total / 2
  let acc = 0
  for (let i = 0; i < points.length - 1; i++) {
    const a = points[i], b = points[i + 1]
    const seg = dist(a, b)
    if (acc + seg >= half) {
      const t = (half - acc) / seg
      return { x: a.x + t * (b.x - a.x), y: a.y + t * (b.y - a.y), angleRad: Math.atan2(b.y - a.y, b.x - a.x) }
    }
    acc += seg
  }
  const last = points[points.length - 1]
  return { x: last.x, y: last.y, angleRad: 0 }
}
