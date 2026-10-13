// Site context → scene geometry (WP5). Imported by SiteCanvas ONLY (it
// imports three); listed in the bundle guard's SiteCanvas-only set.
//
// The backend caches one `context.json` per site (services/site_context.py):
// OSM building footprints with a height, lines (roads, rail, fences, power
// lines), areas (water, substations, land use) and a Terrarium height grid.
// This module turns that document into BufferGeometries in the scene frame
// (x = east, y = up, z = −north; site3d/scene.ts) and answers the ground
// height under a point so assets and ribbons sit on the terrain.
//
// Everything here is pure and jsdom-testable except `hillshadeCanvas`,
// which needs a 2D canvas.

import { BufferGeometry, Float32BufferAttribute, ShapeUtils, Vector2 } from 'three'
import { fromLocal, toLocal, type LngLat, type LocalExtent, type LocalXY } from './geo'
import type { ContextArea, ContextAreaKind, ContextBuilding, ContextLine, ContextLineKind, LngLatTuple, SiteContext, TerrainGrid } from './types'

export interface Parts { positions: number[]; indices: number[] }
export type HeightAt = (x: number, y: number) => number

/** Ribbon width per line kind, metres (a two-lane service road, a track bed, a fence line, a wire's shadow). */
export const RIBBON_WIDTH_M: Record<ContextLineKind, number> = { road: 6, rail: 3, fence: 0.6, power_line: 1.2 }
export const RIBBON_COLOR: Record<ContextLineKind, string> = { road: '#4b5563', rail: '#7c6f64', fence: '#9ca3af', power_line: '#6b7280' }
export const AREA_COLOR: Record<ContextAreaKind, string> = { water: '#7fb3d5', power_substation: '#c7bfe6', landuse: '#000000' }
export const BUILDING_COLOR = '#c9c3b8'
/** ODbL credit, shown whenever context data is drawn (not read from the cached document). */
export const OSM_ATTRIBUTION = '© OpenStreetMap contributors (ODbL)'

/**
 * Lifts above the ground so co-planar surfaces never z-fight: areas, then
 * ribbons, then the boundary line. Areas and ribbons take the ground height
 * at their OWN vertices, so on a slope a long flat polygon can still dip
 * under the displaced ground between them; the lifts cover the gentle case,
 * a Phase 2 item densifies the polygons for the steep one.
 */
const AREA_LIFT_M = 0.3
const RIBBON_LIFT_M = 0.45

// ── footprints → prisms ─────────────────────────────────────────────────────

function dedupeRing(ring: LocalXY[]): LocalXY[] {
  const out: LocalXY[] = []
  for (const p of ring) {
    const last = out[out.length - 1]
    if (last && Math.abs(last.x - p.x) < 1e-6 && Math.abs(last.y - p.y) < 1e-6) continue
    out.push(p)
  }
  const a = out[0], z = out[out.length - 1]
  if (out.length > 1 && Math.abs(a.x - z.x) < 1e-6 && Math.abs(a.y - z.y) < 1e-6) out.pop()
  return out
}

/** A ring ordered counter-clockwise in the (east, north) plane, so a cap wound from it faces up. */
function ccw(ring: LocalXY[]): LocalXY[] {
  return ShapeUtils.isClockWise(ring.map(p => new Vector2(p.x, p.y))) ? [...ring].reverse() : ring
}

/** earcut through three's ShapeUtils, which wants Vector2 instances (it calls `.equals`). */
function triangulate(ring: LocalXY[]): number[][] {
  return ShapeUtils.triangulateShape(ring.map(p => new Vector2(p.x, p.y)), [])
}

/**
 * Extrude a footprint (metres east/north) into a prism from `base` to
 * `base + height`: the top cap (earcut) and one quad per edge, wound so the
 * normals face out and up. No bottom cap — it is never seen. A ring with
 * fewer than three distinct vertices yields nothing.
 */
export function footprintToParts(ring: LocalXY[], height: number, base = 0): Parts {
  const pts = ccw(dedupeRing(ring))
  if (pts.length < 3) return { positions: [], indices: [] }
  const n = pts.length
  const positions: number[] = []
  for (const p of pts) positions.push(p.x, base, -p.y)
  for (const p of pts) positions.push(p.x, base + height, -p.y)
  const indices: number[] = []
  for (const [a, b, c] of triangulate(pts)) indices.push(n + a, n + b, n + c)
  for (let i = 0; i < n; i++) {
    const j = (i + 1) % n
    indices.push(i, j, n + j, i, n + j, n + i)
  }
  return { positions, indices }
}

function ringToLocal(origin: LngLat, ring: LngLatTuple[]): LocalXY[] {
  return ring.map(([lng, lat]) => toLocal(origin, { lng, lat }))
}

function centroidOf(pts: LocalXY[]): LocalXY {
  let x = 0, y = 0
  for (const p of pts) { x += p.x; y += p.y }
  return { x: x / Math.max(pts.length, 1), y: y / Math.max(pts.length, 1) }
}

/** Concatenate parts into one geometry; flat-shaded (non-indexed) when `flat`, else indexed with smooth normals. */
function assemble(parts: Parts[], flat: boolean): BufferGeometry {
  const positions: number[] = []
  const indices: number[] = []
  for (const p of parts) {
    const offset = positions.length / 3
    for (const v of p.positions) positions.push(v)
    for (const i of p.indices) indices.push(i + offset)
  }
  let g = new BufferGeometry()
  g.setAttribute('position', new Float32BufferAttribute(positions, 3))
  g.setIndex(indices)
  if (flat) {
    const indexed = g
    g = indexed.toNonIndexed()
    indexed.dispose()
  }
  g.computeVertexNormals()
  return g
}

/** Every building as one flat-shaded mesh, each sitting at the ground height under its centroid. */
export function buildingsGeometry(buildings: ContextBuilding[], origin: LngLat, heightAt: HeightAt): BufferGeometry {
  const parts: Parts[] = []
  for (const b of buildings) {
    const ring = ringToLocal(origin, b.polygon)
    const c = centroidOf(ring)
    const p = footprintToParts(ring, Math.max(b.height_m, 0.5), heightAt(c.x, c.y))
    if (p.indices.length) parts.push(p)
  }
  return assemble(parts, true)
}

// ── draping: keep every edge shorter than a terrain cell ────────────────────
//
// Areas and ribbons take the ground height at their own vertices; a long
// flat triangle across a bump would dip under the displaced ground. Both
// are densified to the terrain's cell size before the heights are sampled.

/** Insert points along each segment so no segment is longer than `maxEdge` (no-op for a non-finite limit). */
export function densifyPolyline(points: LocalXY[], maxEdge: number): LocalXY[] {
  if (!Number.isFinite(maxEdge) || maxEdge <= 0 || points.length < 2) return points
  const out: LocalXY[] = [points[0]]
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1], b = points[i]
    const n = Math.max(1, Math.ceil(Math.hypot(b.x - a.x, b.y - a.y) / maxEdge))
    for (let k = 1; k <= n; k++) out.push({ x: a.x + ((b.x - a.x) * k) / n, y: a.y + ((b.y - a.y) * k) / n })
  }
  return out
}

/** Triangles subdivided (midpoint split into four, shared midpoints) until every edge is at most `maxEdge`, capped at `maxTriangles`. */
export function subdivideTriangles(pts: LocalXY[], tris: number[][], maxEdge: number, maxTriangles = 8000): { pts: LocalXY[]; tris: number[][] } {
  if (!Number.isFinite(maxEdge) || maxEdge <= 0) return { pts, tris }
  const verts = [...pts]
  const midpoints = new Map<string, number>()
  const mid = (a: number, b: number): number => {
    const key = a < b ? `${a}:${b}` : `${b}:${a}`
    let m = midpoints.get(key)
    if (m === undefined) {
      m = verts.length
      verts.push({ x: (verts[a].x + verts[b].x) / 2, y: (verts[a].y + verts[b].y) / 2 })
      midpoints.set(key, m)
    }
    return m
  }
  const edge = (a: number, b: number) => Math.hypot(verts[a].x - verts[b].x, verts[a].y - verts[b].y)
  const done: number[][] = []
  const queue = tris.map(t => [...t])
  while (queue.length) {
    const [a, b, c] = queue.pop()!
    const tooLong = edge(a, b) > maxEdge || edge(b, c) > maxEdge || edge(c, a) > maxEdge
    if (!tooLong || done.length + queue.length >= maxTriangles) { done.push([a, b, c]); continue }
    const ab = mid(a, b), bc = mid(b, c), ca = mid(c, a)
    queue.push([a, ab, ca], [ab, b, bc], [ca, bc, c], [ab, bc, ca])
  }
  return { pts: verts, tris: done }
}

// ── lines → ribbons ─────────────────────────────────────────────────────────

/** A flat ribbon of `width` along a polyline: one quad per segment (no mitres), `lift` above the ground. */
export function ribbonFor(points: LocalXY[], width: number, heightAt: HeightAt = () => 0, lift = RIBBON_LIFT_M): Parts {
  const positions: number[] = []
  const indices: number[] = []
  const half = width / 2
  for (let i = 0; i + 1 < points.length; i++) {
    const a = points[i], b = points[i + 1]
    const dx = b.x - a.x, dy = b.y - a.y
    const len = Math.hypot(dx, dy)
    if (len < 1e-6) continue
    // Left-hand normal in the (east, north) plane.
    const nx = (-dy / len) * half, ny = (dx / len) * half
    const base = positions.length / 3
    for (const [px, py] of [[a.x + nx, a.y + ny], [a.x - nx, a.y - ny], [b.x - nx, b.y - ny], [b.x + nx, b.y + ny]]) {
      positions.push(px, heightAt(px, py) + lift, -py)
    }
    // Counter-clockwise seen from above (normal up).
    indices.push(base, base + 1, base + 2, base, base + 2, base + 3)
  }
  return { positions, indices }
}

export interface KindGeometry<K extends string> { kind: K; geometry: BufferGeometry }

/** Ribbons grouped by kind (one material each), in order of first appearance; kinds with nothing drawable are omitted. */
export function linesToRibbons(lines: ContextLine[], origin: LngLat, heightAt: HeightAt, maxEdgeM = Infinity): KindGeometry<ContextLineKind>[] {
  const byKind = new Map<ContextLineKind, Parts[]>()
  for (const l of lines) {
    if (l.points.length < 2) continue
    const p = ribbonFor(densifyPolyline(ringToLocal(origin, l.points), maxEdgeM), RIBBON_WIDTH_M[l.kind] ?? 1, heightAt)
    if (!p.indices.length) continue
    if (!byKind.has(l.kind)) byKind.set(l.kind, [])
    byKind.get(l.kind)!.push(p)
  }
  return [...byKind].map(([kind, parts]) => ({ kind, geometry: assemble(parts, false) }))
}

// ── areas → flat polygons ───────────────────────────────────────────────────

/** Water and substation compounds as flat polygons on the ground; land use is context for the height rule only, not drawn. */
export function areasGeometry(areas: ContextArea[], origin: LngLat, heightAt: HeightAt, maxEdgeM = Infinity): KindGeometry<ContextAreaKind>[] {
  const byKind = new Map<ContextAreaKind, Parts[]>()
  for (const a of areas) {
    if (a.kind === 'landuse') continue
    const ring = ccw(dedupeRing(ringToLocal(origin, a.polygon)))
    if (ring.length < 3) continue
    const { pts, tris } = subdivideTriangles(ring, triangulate(ring), maxEdgeM)
    const positions: number[] = []
    for (const p of pts) positions.push(p.x, heightAt(p.x, p.y) + AREA_LIFT_M, -p.y)
    const indices: number[] = []
    for (const [i, j, k] of tris) indices.push(i, j, k)
    if (!indices.length) continue
    if (!byKind.has(a.kind)) byKind.set(a.kind, [])
    byKind.get(a.kind)!.push({ positions, indices })
  }
  return [...byKind].map(([kind, parts]) => ({ kind, geometry: assemble(parts, false) }))
}

// ── terrain ─────────────────────────────────────────────────────────────────

/** Bilinear height at a lon/lat from the grid (row 0 = north, column 0 = west), clamped to the edge outside it. */
export function groundHeightAt(t: TerrainGrid, p: LngLat): number {
  const [minLng, minLat, maxLng, maxLat] = t.bbox
  const n = t.grid
  if (n < 1 || !t.heights_m.length) return 0
  if (n === 1) return t.heights_m[0]
  const clamp = (v: number) => Math.min(n - 1, Math.max(0, v))
  const u = clamp(((p.lng - minLng) / (maxLng - minLng || 1)) * (n - 1))
  const v = clamp(((maxLat - p.lat) / (maxLat - minLat || 1)) * (n - 1))
  const i0 = Math.floor(u), j0 = Math.floor(v)
  const i1 = Math.min(i0 + 1, n - 1), j1 = Math.min(j0 + 1, n - 1)
  const fu = u - i0, fv = v - j0
  const h = (i: number, j: number) => t.heights_m[j * n + i] ?? 0
  const top = h(i0, j0) * (1 - fu) + h(i1, j0) * fu
  const bottom = h(i0, j1) * (1 - fu) + h(i1, j1) * fu
  return top * (1 - fv) + bottom * fv
}

/** A `heightAt(x, y)` in site metres, relative to `base` (the height at the site origin, so the origin stays at y = 0). */
export function terrainSampler(t: TerrainGrid, origin: LngLat, base: number): HeightAt {
  return (x, y) => groundHeightAt(t, fromLocal(origin, { x, y })) - base
}

/**
 * The ground as a displaced plane over `extent`: (grid+1)² vertices, heights
 * relative to `base`, imagery UVs with v = 1 on the north edge (what the
 * mosaic expects, matching a PlaneGeometry laid flat).
 */
export function heightmapToDisplacement(t: TerrainGrid, origin: LngLat, extent: LocalExtent, base: number): BufferGeometry {
  const seg = Math.max(1, t.grid)
  const w = extent.east - extent.west, d = extent.north - extent.south
  const positions: number[] = []
  const uvs: number[] = []
  for (let r = 0; r <= seg; r++) {
    const north = extent.north - (d * r) / seg
    for (let c = 0; c <= seg; c++) {
      const x = extent.west + (w * c) / seg
      positions.push(x, groundHeightAt(t, fromLocal(origin, { x, y: north })) - base, -north)
      uvs.push(c / seg, 1 - r / seg)
    }
  }
  const indices: number[] = []
  const stride = seg + 1
  for (let r = 0; r < seg; r++) {
    for (let c = 0; c < seg; c++) {
      const a = r * stride + c, b = a + 1, cc = a + stride, dd = cc + 1
      indices.push(a, cc, dd, a, dd, b)
    }
  }
  const g = new BufferGeometry()
  g.setAttribute('position', new Float32BufferAttribute(positions, 3))
  g.setAttribute('uv', new Float32BufferAttribute(uvs, 2))
  g.setIndex(indices)
  g.computeVertexNormals()
  return g
}

/** Approximate metres between neighbouring samples. */
export function terrainCellMetres(t: TerrainGrid): number {
  const [minLng, minLat, maxLng, maxLat] = t.bbox
  const midLat = (minLat + maxLat) / 2
  const perLat = 111_319.5
  const widthM = (maxLng - minLng) * perLat * Math.cos((midLat * Math.PI) / 180)
  const depthM = (maxLat - minLat) * perLat
  const n = Math.max(t.grid - 1, 1)
  return (widthM / n + depthM / n) / 2
}

// ── offline degrade (Task 5.6) ──────────────────────────────────────────────

export type GroundMode = 'imagery' | 'hillshade' | 'flat'

/** What the ground shows: the imagery when it arrived, a hillshade of the cached terrain when it did not, else flat grey. */
export function groundMode(context: SiteContext | null, imageryOk: boolean): GroundMode {
  if (imageryOk) return 'imagery'
  if (context?.terrain) return 'hillshade'
  return 'flat'
}

/**
 * A Lambertian hillshade of the grid, light from the north-west at 45°:
 * grid × grid RGBA, row 0 = north, ready for a canvas `ImageData`.
 */
export function hillshade(t: TerrainGrid, cellM: number): Uint8ClampedArray {
  const n = t.grid
  const out = new Uint8ClampedArray(n * n * 4)
  const h = (i: number, j: number) => t.heights_m[Math.min(n - 1, Math.max(0, j)) * n + Math.min(n - 1, Math.max(0, i))] ?? 0
  const cell = Math.max(cellM, 1e-6)
  // Light toward the north-west, 45° up: (east, north, up).
  const lx = -0.5, ly = 0.5, lz = Math.SQRT1_2
  for (let j = 0; j < n; j++) {
    for (let i = 0; i < n; i++) {
      const dzdx = (h(i + 1, j) - h(i - 1, j)) / (2 * cell)          // rising east
      const dzdy = (h(i, j - 1) - h(i, j + 1)) / (2 * cell)          // rising north (row 0 is north)
      const len = Math.hypot(dzdx, dzdy, 1)
      const nx = -dzdx / len, ny = -dzdy / len, nz = 1 / len
      const shade = Math.max(0, nx * lx + ny * ly + nz * lz)
      const v = 40 + shade * 215
      const o = (j * n + i) * 4
      out[o] = v * 0.92
      out[o + 1] = v
      out[o + 2] = v * 0.86
      out[o + 3] = 255
    }
  }
  return out
}

/** The hillshade as a canvas for a CanvasTexture (browser only). */
export function hillshadeCanvas(t: TerrainGrid): HTMLCanvasElement {
  const canvas = document.createElement('canvas')
  canvas.width = t.grid
  canvas.height = t.grid
  const ctx = canvas.getContext('2d')
  if (ctx) ctx.putImageData(new ImageData(hillshade(t, terrainCellMetres(t)), t.grid, t.grid), 0, 0)
  return canvas
}
