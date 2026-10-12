// Placement geometry and the placement check (visual-layers plan 3 S2,
// owner decision O1: placement carries meaning).
//
// The geometry half is what the packer (layout.ts) and the check share: an
// object's footprint as an axis-aligned rectangle after its heading, the
// distance between two such rectangles, the segment between two yards a
// `between` object should sit on, and bearings. The check half takes a
// built layout's objects and the library and says, per object, where the
// arrangement breaks a rule — findings a user can read, never a block:
// the user may leave an implausible layout and nothing stops a save.
//
// Pure: no React, no three (the Issues panel in the main bundle imports it).

import { footprintCorners } from './fit'
import { PACKING, type AssetType, type PlacementRule } from './assetLibrary'

// ── geometry ────────────────────────────────────────────────────────────────

export interface Rect { x0: number; x1: number; y0: number; y1: number }
export type XY = [number, number]

/** What the geometry needs of an object: where it stands, its unrotated footprint, its heading. */
export interface PlacedShape { origin: XY; footprint: XY; heading: number }

/** The axis-aligned rectangle enclosing the footprint after rotation by `heading`. */
export function rectOf(o: PlacedShape): Rect {
  const c = footprintCorners(o)
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity
  for (const p of c) { x0 = Math.min(x0, p.x); x1 = Math.max(x1, p.x); y0 = Math.min(y0, p.y); y1 = Math.max(y1, p.y) }
  return { x0, x1, y0, y1 }
}

/** East and north extents of a rectangle. */
export const rectSize = (r: Rect): XY => [r.x1 - r.x0, r.y1 - r.y0]
export const rectCentre = (r: Rect): XY => [(r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2]

/** Strict interior overlap (touching edges do not overlap). */
export function rectsOverlap(a: Rect, b: Rect): boolean {
  return a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1
}

/** The shortest distance between two rectangles' edges; 0 when they touch or overlap. */
export function rectDistance(a: Rect, b: Rect): number {
  const dx = Math.max(0, a.x0 - b.x1, b.x0 - a.x1)
  const dy = Math.max(0, a.y0 - b.y1, b.y0 - a.y1)
  return Math.hypot(dx, dy)
}

/** Degrees clockwise from north of the direction from `from` to `to` (0 when they coincide). */
export function bearingDeg(from: XY, to: XY): number {
  const dx = to[0] - from[0], dy = to[1] - from[1]
  if (dx === 0 && dy === 0) return 0
  const deg = (Math.atan2(dx, dy) * 180) / Math.PI
  return ((deg % 360) + 360) % 360
}

/** The smaller angle between two headings, 0..180. */
export function angleDiffDeg(a: number, b: number): number {
  return Math.abs(((a - b) % 360 + 540) % 360 - 180)
}

/** The unit vector of a heading (degrees clockwise from north) as (east, north). */
export const headingVector = (headingDeg: number): XY => {
  const a = (headingDeg * Math.PI) / 180
  return [Math.sin(a), Math.cos(a)]
}

/** Where a ray from a rectangle's centre in direction `dir` (unit) leaves it. */
function exitPoint(r: Rect, dir: XY): XY {
  const [cx, cy] = rectCentre(r)
  const [w, d] = rectSize(r)
  const tx = dir[0] === 0 ? Infinity : (w / 2) / Math.abs(dir[0])
  const ty = dir[1] === 0 ? Infinity : (d / 2) / Math.abs(dir[1])
  const t = Math.min(tx, ty)
  return [cx + dir[0] * t, cy + dir[1] * t]
}

/**
 * The segment a `between` object sits on: from the owner yard's edge to the
 * far yard's edge along the line through their centres. Null when the yards
 * touch or overlap (there is no "between") or share a centre.
 */
export function betweenSegment(owner: Rect, far: Rect): { a: XY; b: XY; dir: XY } | null {
  const oc = rectCentre(owner), fc = rectCentre(far)
  const dx = fc[0] - oc[0], dy = fc[1] - oc[1]
  const len = Math.hypot(dx, dy)
  if (len === 0) return null
  const dir: XY = [dx / len, dy / len]
  const a = exitPoint(owner, dir)
  const b = exitPoint(far, [-dir[0], -dir[1]])
  if ((b[0] - a[0]) * dir[0] + (b[1] - a[1]) * dir[1] <= 0) return null
  return { a, b, dir }
}

/** Distance from a point to a segment. */
export function distanceToSegment(p: XY, a: XY, b: XY): number {
  const abx = b[0] - a[0], aby = b[1] - a[1]
  const len2 = abx * abx + aby * aby
  const t = len2 === 0 ? 0 : Math.max(0, Math.min(1, ((p[0] - a[0]) * abx + (p[1] - a[1]) * aby) / len2))
  return Math.hypot(p[0] - (a[0] + t * abx), p[1] - (a[1] + t * aby))
}

const inRect = (p: XY, r: Rect): boolean => p[0] >= r.x0 && p[0] <= r.x1 && p[1] >= r.y0 && p[1] <= r.y1

function segmentsCross(p1: XY, p2: XY, q1: XY, q2: XY): boolean {
  const cross = (o: XY, a: XY, b: XY) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
  const d1 = cross(q1, q2, p1), d2 = cross(q1, q2, p2), d3 = cross(p1, p2, q1), d4 = cross(p1, p2, q2)
  return ((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) && ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0))
}

/**
 * Distance between a segment and a rectangle: 0 when the segment crosses or
 * ends inside it (a `between` object the inter-yard line passes through sits
 * between its buses, however wide it is).
 */
export function segmentRectDistance(a: XY, b: XY, r: Rect): number {
  if (inRect(a, r) || inRect(b, r)) return 0
  const corners: XY[] = [[r.x0, r.y0], [r.x1, r.y0], [r.x1, r.y1], [r.x0, r.y1]]
  for (let i = 0; i < 4; i++) if (segmentsCross(a, b, corners[i], corners[(i + 1) % 4])) return 0
  let best = Infinity
  for (const c of corners) best = Math.min(best, distanceToSegment(c, a, b))
  for (const p of [a, b]) best = Math.min(best, rectDistance({ x0: p[0], x1: p[0], y0: p[1], y1: p[1] }, r))
  return best
}

// ── rules between two objects ───────────────────────────────────────────────

/** What two types must keep between their footprints: a keep-out (either rule naming the other, or `'*'`) and a clearance (either rule's). */
export function pairRequirement(aKind: string, aRule: PlacementRule | undefined, bKind: string, bRule: PlacementRule | undefined): { keepOutM: number; clearanceM: number } {
  let keepOutM = 0
  for (const k of aRule?.keepOutM ?? []) if (k.from.includes('*') || k.from.includes(bKind)) keepOutM = Math.max(keepOutM, k.m)
  for (const k of bRule?.keepOutM ?? []) if (k.from.includes('*') || k.from.includes(aKind)) keepOutM = Math.max(keepOutM, k.m)
  return { keepOutM, clearanceM: Math.max(aRule?.clearanceM ?? 0, bRule?.clearanceM ?? 0) }
}

/** The rule for a type id in a library (undefined when the type has none). */
export const ruleLookup = (lib: readonly AssetType[]) => {
  const m = new Map(lib.map(t => [t.id, t.placement]))
  return (kind: string): PlacementRule | undefined => m.get(kind)
}

// ── the check ───────────────────────────────────────────────────────────────

export type FindingKind = 'outsideBoundary' | 'overlap' | 'keepOut' | 'clearance' | 'notBetweenBuses' | 'notFacingFar'

export interface PlacementFinding {
  /** The object the finding is about (`"<Class>:<name>"`). */
  key: string
  kind: FindingKind
  /** `warn`: outside the plot, overlapping, inside a keep-out. `info`: a clearance, a position or a bearing the rule would rather see otherwise. */
  severity: 'warn' | 'info'
  /** Written for the user: names, metres, degrees. */
  message: string
  /** The other object of a pair (overlap, keep-out, clearance) or the yard the object should face. */
  other?: string
  /** The measured distance, rounded, where one applies. */
  distanceM?: number
}

/** What the check needs of a layout object (a `SiteObject` has all of it). */
export interface CheckObject extends PlacedShape {
  type: string
  name: string
  kind: string
  bus: string
  far?: string
  /** Set for an object standing on a roof: exempt from every finding. */
  elevation?: number
}

const keyOf = (o: Pick<CheckObject, 'type' | 'name'>): string => `${o.type}:${o.name}`
/** How an object is named in a message: a yard by its bus. */
const nameOf = (o: Pick<CheckObject, 'type' | 'name'>): string => (o.type === 'Bus' ? `the ${o.name} yard` : o.name)
const capital = (s: string): string => s.charAt(0).toUpperCase() + s.slice(1)
const metres = (v: number): number => Math.round(v)

/** The outline colour a finding kind earns: red for the two that matter most on the ground, amber for the rest. */
export const findingColor = (kind: FindingKind): 'red' | 'amber' => (kind === 'outsideBoundary' || kind === 'keepOut' ? 'red' : 'amber')

/** The keys a set of findings marks: each finding's object, and the other object of a pair. */
export function findingKeys(findings: readonly PlacementFinding[], color?: 'red' | 'amber'): Set<string> {
  const out = new Set<string>()
  for (const f of findings) {
    if (color && findingColor(f.kind) !== color) continue
    out.add(f.key)
    if (f.other && (f.kind === 'overlap' || f.kind === 'keepOut' || f.kind === 'clearance')) out.add(f.other)
  }
  return out
}

/**
 * Every way the arrangement breaks a placement rule, per object: outside the
 * boundary (`outside` is the fit check's list of keys), overlapping another
 * object, inside a keep-out, short of a clearance, a `between` object off
 * the line between its yards, a `faceFar` object turned away from its far
 * yard. A pair is reported once, on the object whose rule it is (the first
 * of the two when both or neither have one). Rooftop objects are exempt.
 * Deterministic for a given input.
 */
export function placementFindings(objects: readonly CheckObject[], lib: readonly AssetType[], opts: { outside?: readonly string[] } = {}): PlacementFinding[] {
  const ruleOf = ruleLookup(lib)
  const solid = objects.filter(o => !o.elevation)
  const rects = new Map(solid.map(o => [o, rectOf(o)]))
  const yards = new Map(solid.filter(o => o.type === 'Bus').map(o => [o.name, o]))
  const outside = new Set(opts.outside ?? [])
  const out: PlacementFinding[] = []

  for (let i = 0; i < solid.length; i++) {
    const a = solid[i], ar = rects.get(a)!, aRule = ruleOf(a.kind)
    if (outside.has(keyOf(a))) {
      out.push({ key: keyOf(a), kind: 'outsideBoundary', severity: 'warn', message: `${capital(nameOf(a))} sticks out of the site boundary` })
    }
    for (let j = i + 1; j < solid.length; j++) {
      const b = solid[j], br = rects.get(b)!, bRule = ruleOf(b.kind)
      if (rectsOverlap(ar, br)) {
        out.push({ key: keyOf(a), other: keyOf(b), kind: 'overlap', severity: 'warn', message: `${capital(nameOf(a))} overlaps ${nameOf(b)}` })
        continue
      }
      const d = rectDistance(ar, br)
      const ka = pairRequirement(a.kind, aRule, b.kind, undefined).keepOutM, kb = pairRequirement(b.kind, bRule, a.kind, undefined).keepOutM
      const keepOut = Math.max(ka, kb)
      if (keepOut > 0 && d < keepOut) {
        const [who, whom] = kb > ka ? [b, a] : [a, b]
        out.push({ key: keyOf(who), other: keyOf(whom), kind: 'keepOut', severity: 'warn', distanceM: metres(d), message: `${capital(nameOf(who))} is ${metres(d)} m from ${nameOf(whom)}; it should keep ${keepOut} m away` })
        continue
      }
      const ca = aRule?.clearanceM ?? 0, cb = bRule?.clearanceM ?? 0
      const clearance = Math.max(ca, cb)
      if (clearance > 0 && d < clearance) {
        const [who, whom] = cb > ca ? [b, a] : [a, b]
        out.push({ key: keyOf(who), other: keyOf(whom), kind: 'clearance', severity: 'info', distanceM: metres(d), message: `${capital(nameOf(who))} is ${metres(d)} m from ${nameOf(whom)}; it needs ${clearance} m clear` })
      }
    }
    const owner = yards.get(a.bus), far = a.far ? yards.get(a.far) : undefined
    if (aRule?.anchor === 'between' && owner && far && owner !== far) {
      const seg = betweenSegment(rects.get(owner)!, rects.get(far)!)
      const d = seg ? segmentRectDistance(seg.a, seg.b, ar) : 0
      if (seg && d > PACKING.betweenToleranceM) {
        out.push({ key: keyOf(a), kind: 'notBetweenBuses', severity: 'info', distanceM: metres(d), message: `${capital(nameOf(a))} is ${metres(d)} m from the line between the ${owner.name} and ${far.name} yards; it should sit between its two buses` })
      }
    }
    if (aRule?.orientation === 'faceFar' && far && far !== a) {
      const off = angleDiffDeg(a.heading, bearingDeg(a.origin, far.origin))
      if (off > PACKING.facingToleranceDeg) {
        out.push({ key: keyOf(a), other: keyOf(far), kind: 'notFacingFar', severity: 'info', message: `${capital(nameOf(a))} faces ${Math.round(off)}° away from ${nameOf(far)}; it should face it` })
      }
    }
  }
  return out
}
