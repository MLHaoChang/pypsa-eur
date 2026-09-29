// The few pure decisions the 3D canvas makes, kept out of the component so
// they are testable without WebGL (jsdom has none — see the assessment §7).

import type { Bus } from '../api/types'
import { isPlaced } from '../utils/geo'
import type { Site, SitesDocument } from './types'

/** Local (east, north, up) metres → three.js (x, y, z) with Y up and north = −Z. */
export function toScene(east: number, north: number, up: number): [number, number, number] {
  return [east, up, -north]
}

/** Box size (east extent, north extent, height) → boxGeometry args (width x, height y, depth z). */
export function toBoxArgs(size: [number, number, number]): [number, number, number] {
  return [size[0], size[2], size[1]]
}

export interface Bounds { x0: number; x1: number; y0: number; y1: number }

/**
 * A camera south of the site, tilted down, far enough back that the whole
 * packed bounds fit the viewport at its real aspect ratio. From the south,
 * east–west spread maps to screen x and north to screen depth, which is what a
 * plan-reading user expects; a fixed south-west vantage put half the site off
 * the left edge whenever the canvas column was narrow (assistant dock + tab
 * panel open leave it ~440 px wide, portrait).
 */
export function fitCamera(bounds: Bounds, aspect: number, fovDeg = 45, tiltDeg = 50): {
  position: [number, number, number]; target: [number, number, number]; far: number
} {
  const margin = 40
  const halfW = (bounds.x1 - bounds.x0) / 2 + margin
  const halfD = (bounds.y1 - bounds.y0) / 2 + margin
  const cx = (bounds.x0 + bounds.x1) / 2
  const cy = (bounds.y0 + bounds.y1) / 2
  const halfFov = (fovDeg * Math.PI) / 360
  const tilt = (tiltDeg * Math.PI) / 180
  const a = Math.max(aspect, 0.1)
  // Width must fit the horizontal half-angle; depth is foreshortened by the
  // tilt, so its on-screen extent is roughly halfD·sin(tilt) plus the near
  // edge's rise — 1.25 covers both without a second pass.
  const dist = Math.max(halfW / (Math.tan(halfFov) * a), (halfD * Math.sin(tilt) + halfD * 0.35) / Math.tan(halfFov)) * 1.25
  const back = dist * Math.cos(tilt)
  const up = dist * Math.sin(tilt)
  return {
    position: toScene(cx, cy - back, up),
    target: toScene(cx, cy, 0),
    far: Math.max(dist * 20, 5000),
  }
}

/**
 * Which site the 3D view shows: the one the user chose, else the site that
 * contains the selected component's bus, else the first. `null` with no
 * sites — the view then offers to create one.
 */
export function chooseSite(
  doc: SitesDocument,
  activeSiteId: string | null,
  selectedBus: string | null,
): Site | null {
  if (doc.sites.length === 0) return null
  if (activeSiteId) {
    const s = doc.sites.find(x => x.id === activeSiteId)
    if (s) return s
  }
  if (selectedBus) {
    const s = doc.sites.find(x => x.buses.includes(selectedBus))
    if (s) return s
  }
  return doc.sites[0]
}

/** The smallest bounds containing both. */
export function unionBounds(a: Bounds, b: Bounds): Bounds {
  return { x0: Math.min(a.x0, b.x0), x1: Math.max(a.x1, b.x1), y0: Math.min(a.y0, b.y0), y1: Math.max(a.y1, b.y1) }
}

/** Half-size of the square about the frame origin that contains `b` (min 250 m, 50 m steps). */
export function halfSizeFor(b: Bounds): number {
  const reach = Math.max(250, Math.abs(b.x0), Math.abs(b.x1), Math.abs(b.y0), Math.abs(b.y1))
  return Math.ceil((reach * 1.15) / 50) * 50
}
