// Boundary polygon geometry for a site: area, centroid, containment, and
// the default rectangle "Create a site around bus X" draws.
//
// Pure — no React, no Leaflet, no three. Everything metric is computed on
// the tangent plane at the site origin (site3d/geo.ts), so a polygon's area
// is a real area in m², not degrees². No `three` import: this module is in
// the main bundle (the map view reads it).

import { toLocal, fromLocal, type LngLat, type LocalXY } from './geo'
import { isPlaced } from '../utils/geo'
import type { LngLatTuple } from './types'

export interface BoundsXY { x0: number; x1: number; y0: number; y1: number }

const asLngLat = (v: LngLatTuple): LngLat => ({ lng: v[0], lat: v[1] })

/** The boundary projected to metres from `origin`. */
export function boundaryToLocal(boundary: LngLatTuple[], origin: LngLat): LocalXY[] {
  return boundary.map(v => toLocal(origin, asLngLat(v)))
}

/** Shoelace on the tangent plane at the polygon's own centroid; always ≥ 0. */
export function polygonAreaM2(boundary: LngLatTuple[]): number {
  if (boundary.length < 3) return 0
  const pts = boundaryToLocal(boundary, centroid(boundary))
  let twice = 0
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i], b = pts[(i + 1) % pts.length]
    twice += a.x * b.y - b.x * a.y
  }
  return Math.abs(twice) / 2
}

/**
 * Area-weighted centroid in lon/lat. Degenerate (zero-area) polygons fall
 * back to the vertex mean so a site never gets a NaN origin.
 */
export function centroid(boundary: LngLatTuple[]): LngLat {
  const n = boundary.length
  const mean: LngLat = {
    lng: boundary.reduce((a, v) => a + v[0], 0) / n,
    lat: boundary.reduce((a, v) => a + v[1], 0) / n,
  }
  if (n < 3) return mean
  const pts = boundaryToLocal(boundary, mean)
  let twice = 0, cx = 0, cy = 0
  for (let i = 0; i < n; i++) {
    const a = pts[i], b = pts[(i + 1) % n]
    const cross = a.x * b.y - b.x * a.y
    twice += cross
    cx += (a.x + b.x) * cross
    cy += (a.y + b.y) * cross
  }
  if (Math.abs(twice) < 1e-9) return mean
  const area = twice / 2
  return fromLocal(mean, { x: cx / (6 * area), y: cy / (6 * area) })
}

/**
 * Ray casting in local metres. A point on an edge or a vertex counts as
 * inside — a bus placed exactly on the fence belongs to the site.
 */
export function pointInPolygonLocal(p: LocalXY, poly: LocalXY[]): boolean {
  const n = poly.length
  if (n < 3) return false
  let inside = false
  for (let i = 0, j = n - 1; i < n; j = i++) {
    const a = poly[i], b = poly[j]
    if (onSegment(p, a, b)) return true
    const crosses = (a.y > p.y) !== (b.y > p.y)
    if (crosses) {
      const x = a.x + ((p.y - a.y) * (b.x - a.x)) / (b.y - a.y)
      if (p.x < x) inside = !inside
    }
  }
  return inside
}

function onSegment(p: LocalXY, a: LocalXY, b: LocalXY, eps = 1e-6): boolean {
  const cross = (b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x)
  if (Math.abs(cross) > eps * Math.max(1, Math.hypot(b.x - a.x, b.y - a.y))) return false
  return p.x >= Math.min(a.x, b.x) - eps && p.x <= Math.max(a.x, b.x) + eps
    && p.y >= Math.min(a.y, b.y) - eps && p.y <= Math.max(a.y, b.y) + eps
}

export function pointInBoundary(point: LngLat, boundary: LngLatTuple[]): boolean {
  if (boundary.length < 3) return false
  const origin = centroid(boundary)
  return pointInPolygonLocal(toLocal(origin, point), boundaryToLocal(boundary, origin))
}

/** Names of the placed buses inside the boundary, in the order given. */
export function busesInside<T extends { name: string; x: number | null | undefined; y: number | null | undefined }>(
  buses: T[], boundary: LngLatTuple[],
): string[] {
  if (boundary.length < 3) return []
  const origin = centroid(boundary)
  const poly = boundaryToLocal(boundary, origin)
  return buses
    .filter(isPlaced)
    .filter(b => pointInPolygonLocal(toLocal(origin, { lng: Number(b.x), lat: Number(b.y) }), poly))
    .map(b => b.name)
}

/**
 * The rectangle "Create a site around bus X" uses: the packed layout's
 * bounds (metres from `origin`) grown by 20 %, as four clockwise lon/lat
 * vertices starting north-west. Its centroid is the centre of `bounds`,
 * which is NOT the origin when the layout packs to one side.
 */
export function defaultBoundaryFor(bounds: BoundsXY, origin: LngLat, grow = 0.2): LngLatTuple[] {
  const w = bounds.x1 - bounds.x0, d = bounds.y1 - bounds.y0
  const gx = Math.max(w * grow, 20), gy = Math.max(d * grow, 20)
  const x0 = bounds.x0 - gx / 2, x1 = bounds.x1 + gx / 2
  const y0 = bounds.y0 - gy / 2, y1 = bounds.y1 + gy / 2
  const corners: LocalXY[] = [{ x: x0, y: y1 }, { x: x1, y: y1 }, { x: x1, y: y0 }, { x: x0, y: y0 }]
  return corners.map(c => { const ll = fromLocal(origin, c); return [ll.lng, ll.lat] as LngLatTuple })
}

/** ≥ 3 vertices, finite, in range, no two consecutive vertices equal (closing duplicate included). */
export function isValidBoundary(boundary: LngLatTuple[]): boolean {
  if (!Array.isArray(boundary) || boundary.length < 3) return false
  for (let i = 0; i < boundary.length; i++) {
    const v = boundary[i]
    if (!Array.isArray(v) || v.length !== 2 || !Number.isFinite(v[0]) || !Number.isFinite(v[1])) return false
    if (v[0] < -180 || v[0] > 180 || v[1] < -90 || v[1] > 90) return false
    const next = boundary[(i + 1) % boundary.length]
    if (v[0] === next[0] && v[1] === next[1]) return false
  }
  return true
}

/**
 * Drop trailing vertices equal to the one before them — what a double-click
 * close produces (Leaflet fires two clicks before `dblclick`).
 */
export function dedupeTrailing(vertices: LngLatTuple[]): LngLatTuple[] {
  const out = vertices.slice()
  while (out.length >= 2) {
    const a = out[out.length - 1], b = out[out.length - 2]
    if (a[0] === b[0] && a[1] === b[1]) out.pop()
    else break
  }
  return out
}

/** Axis-aligned bounds of the boundary in metres from `origin`. */
export function boundaryBounds(boundary: LngLatTuple[], origin: LngLat): BoundsXY {
  const pts = boundaryToLocal(boundary, origin)
  return {
    x0: Math.min(...pts.map(p => p.x)), x1: Math.max(...pts.map(p => p.x)),
    y0: Math.min(...pts.map(p => p.y)), y1: Math.max(...pts.map(p => p.y)),
  }
}
