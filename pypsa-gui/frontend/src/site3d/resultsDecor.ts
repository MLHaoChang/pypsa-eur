// The results layer's decisions (Phase 2 spec §6.4), pure and no three so
// they are unit-tested (WebGL cannot run in vitest): when an object's
// decorations must be rebuilt, the gauge bar, the chevron path, and the
// result glow with its precedence. resultsLayer.tsx only applies them.
import type { Anchors } from './templates'
import { emissiveFor, type Visual } from './resultStyle'

type V3 = [number, number, number]

/** The gauge and flow anchors as a string: a change means new decorations (resized units, an as built / optimised flip). */
export function anchorsSignature(a: Anchors | undefined): string {
  if (!a?.fill && !a?.flow) return ''
  return JSON.stringify([a.fill ?? null, a.flow ?? null])
}

/** The fill bar inside a track of `width`: its x scale and centre, growing from the west end. */
export function gaugeBar(fill: number, width: number): { scaleX: number; x: number } {
  const f = Math.min(1, Math.max(0.001, fill))
  return { scaleX: f, x: -width / 2 + (width * f) / 2 }
}

/** Where `n` chevrons sit along the flow anchor (scene coordinates) and which way they point: `dir` +1 from → to, −1 back, 0 none. */
export function chevronPath(from: V3, to: V3, dir: 1 | -1 | 0, phase: number, n: number): { points: V3[]; heading: V3 } {
  if (dir === 0) return { points: [], heading: [0, 0, 0] }
  const [a, b] = dir === 1 ? [from, to] : [to, from]
  const d: V3 = [b[0] - a[0], b[1] - a[1], b[2] - a[2]]
  const len = Math.hypot(...d) || 1
  const points: V3[] = []
  for (let k = 0; k < n; k++) {
    const t = (((phase + k / n) % 1) + 1) % 1
    points.push([a[0] + d[0] * t, a[1] + d[1] * t, a[2] + d[2] * t])
  }
  return { points, heading: [d[0] / len, d[1] / len, d[2] / len] }
}

/**
 * The glow a result gives an object, or null when selection, hover or the
 * boundary warning own it (precedence, emissiveFor). A share glows in the
 * object's colour at its eased value; a branch in its loading band, brighter
 * with loading; no result → off.
 */
export function resultGlow(locked: boolean, v: Visual | undefined, eased: number, color: string): { color: string; intensity: number } | null {
  if (locked) return null
  if (v?.color) return { color: v.color, intensity: 0.1 + 0.35 * Math.min(1, Math.max(0, (v.flow?.pct ?? 0) / 100)) }
  return emissiveFor({ selected: false, hovered: false, outside: false }, color, v?.emissive != null ? eased : undefined)
}
