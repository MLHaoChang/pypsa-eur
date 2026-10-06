// ── The schematic's auto-layout (pure) ────────────────────────────────────────
// `runLayout` used to live inside TopologyCanvas.tsx, where nothing tested it
// (visual-layers plan 1, A5). It is the "latent coordinates" generator: buses
// in three voltage-tier rows, jittered deterministically by name hash, pushed
// apart until no two overlap, centred on the canvas; then every satellite (an
// asset-group bubble, or an individual asset node in A2's *individual* mode)
// at its offset from its bus. No React, no React Flow, no `bus.x`/`bus.y` —
// the blank canvas never seeds from geographic coordinates.
import { safeMinMax } from '../utils/numeric'

/** Half-diagonal of the rounded-rect bus node (~88 px wide × 30 px tall). */
export const BUS_R = 44
/** Half-diagonal of a ~100 × 100 asset card (group bubble or asset node). */
export const ASSET_R = 62

/** FNV-1a over UTF-16 code units: a stable 32-bit hash of a name. */
export function hashStr(s: string): number {
  let h = 0x811c9dc5
  for (let i = 0; i < s.length; i++) {
    h = Math.imul(h ^ s.charCodeAt(i), 0x01000193) >>> 0
  }
  return h
}

/** A small deterministic PRNG (mulberry32) seeded by `hashStr`; yields [0, 1). */
export function mulberry32(seed: number): () => number {
  let s = seed >>> 0
  return () => {
    s += 0x6d2b79f5
    let x = Math.imul(s ^ (s >>> 15), 1 | s)
    x ^= x + Math.imul(x ^ (x >>> 7), 61 | x)
    return ((x ^ (x >>> 14)) >>> 0) / 0xffffffff
  }
}

export interface LayoutBus { id: string; v_nom: number }
/** A node drawn beside a bus at a fixed offset from it. */
export interface LayoutSatellite { id: string; busId: string; dx: number; dy: number }
export type LayoutPositions = Record<string, { x: number; y: number }>

/** The voltage tier a bus sits in: 0 = ≥ 220 kV (top row), 1 = ≥ 50 kV, 2 = the rest (bottom row). */
export const tierOf = (vNom: number): 0 | 1 | 2 => (vNom >= 220 ? 0 : vNom >= 50 ? 1 : 2)

export const LAYOUT_W = 1400
export const LAYOUT_H = 900
const BUS_SEP = 210
/** Buses closer than this (centre to centre) are pushed apart. */
export const BUS_MIN_DIST = BUS_R * 2 + 32

/**
 * Bus positions by tier row, pushed apart, centred; satellites at their
 * offsets. Deterministic: the same input gives the same output.
 */
export function runLayout(
  buses: readonly LayoutBus[],
  satellites: readonly LayoutSatellite[] = [],
  W = LAYOUT_W, H = LAYOUT_H,
): LayoutPositions {
  if (buses.length === 0) return {}
  const TIER_Y = [H * 0.2, H * 0.5, H * 0.8]

  const tierGroups: string[][] = [[], [], []]
  buses.forEach(b => { tierGroups[tierOf(b.v_nom)].push(b.id) })

  const pos: LayoutPositions = {}
  tierGroups.forEach((group, tier) => {
    if (group.length === 0) return
    const startX = W / 2 - ((group.length - 1) * BUS_SEP) / 2
    group.forEach((id, i) => {
      const rand = mulberry32(hashStr(id))
      pos[id] = {
        x: startX + i * BUS_SEP + (rand() - 0.5) * 40,
        y: TIER_Y[tier] + (rand() - 0.5) * 60,
      }
    })
  })

  // Collision resolution — push overlapping bus nodes apart.
  const ids = buses.map(b => b.id)
  for (let iter = 0; iter < 50; iter++) {
    let moved = false
    for (let i = 0; i < ids.length; i++) {
      for (let j = i + 1; j < ids.length; j++) {
        const a = pos[ids[i]], b = pos[ids[j]]
        const dx = b.x - a.x, dy = b.y - a.y
        const dist = Math.sqrt(dx * dx + dy * dy) || 0.01
        if (dist < BUS_MIN_DIST) {
          const push = (BUS_MIN_DIST - dist) / 2 + 1
          const nx = (dx / dist) * push, ny = (dy / dist) * push
          a.x -= nx; a.y -= ny; b.x += nx; b.y += ny
          moved = true
        }
      }
    }
    if (!moved) break
  }

  // Centre around the canvas origin (single-pass min/max — see utils/numeric.ts).
  const xs = ids.map(id => pos[id].x), ys = ids.map(id => pos[id].y)
  const xmm = safeMinMax(xs), ymm = safeMinMax(ys)
  const cx = (xmm.min + xmm.max) / 2
  const cy = (ymm.min + ymm.max) / 2
  const offX = W / 2 - cx, offY = H / 2 - cy
  ids.forEach(id => { pos[id].x += offX; pos[id].y += offY })

  // Satellites relative to their bus (a satellite of an unknown bus is skipped).
  satellites.forEach(s => {
    const bus = pos[s.busId]
    if (!bus) return
    pos[s.id] = { x: bus.x + s.dx, y: bus.y + s.dy }
  })

  return pos
}
