// SC-01b: pure helpers for a native Site 3D scene (a Site with no buses).
// No `three` import: the panels use these too.
import { fromLocal, toLocal, type LngLat, type LocalXY } from './geo'
import { pointInPolygonLocal } from './boundary'
import { siteBounds } from './siteModel'
import type { SiteLayout } from './layout'
import type { ContextBuilding, LngLatTuple, Site } from './types'
import type { GreenfieldInput, SceneBuilding, SourceInput } from '../api/siteScene'

/** The layout of a Site with no model equipment: nothing packed, framed by its own boundary. */
export function emptyLayoutFor(site: Site): SiteLayout {
  const b = siteBounds(site)
  return {
    objects: [], halfSizeM: 250, centre: [(b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2], bounds: b,
    totalAreaM2: 0, orphans: [], placed: [], unresolved: [],
  }
}

/** A `width × depth` rectangle about `center` (metres), heading clockwise from north (frames.rectangle). */
export function rectangleLocal(center: LocalXY, width: number, depth: number, headingDeg: number): LocalXY[] {
  const h = (headingDeg * Math.PI) / 180
  const fwd = { x: Math.sin(h), y: Math.cos(h) }
  const right = { x: Math.cos(h), y: -Math.sin(h) }
  const corner = (r: number, f: number) => ({
    x: center.x + right.x * r * width / 2 + fwd.x * f * depth / 2,
    y: center.y + right.y * r * width / 2 + fwd.y * f * depth / 2,
  })
  return [corner(-1, -1), corner(1, -1), corner(1, 1), corner(-1, 1)]
}

/** The "New 3D site" boundary inputs and their limits (visible as text in the panel). */
export interface BoundaryInputs { width: number; depth: number; east: number; north: number; heading: number }
export const BOUNDARY_DEFAULTS: BoundaryInputs = { width: 200, depth: 200, east: 0, north: 0, heading: 0 }
export const SIDE_MIN_M = 10
export const SIDE_MAX_M = 5000
/** D01's anchor_moved threshold: an offset beyond it would flag the new Site at once. */
export const OFFSET_MAX_M = 1000

export function boundaryErrors(v: BoundaryInputs): Partial<Record<keyof BoundaryInputs, string>> {
  const out: Partial<Record<keyof BoundaryInputs, string>> = {}
  const side = `Enter ${SIDE_MIN_M}–${SIDE_MAX_M.toLocaleString('en-US')} m`
  if (!(v.width >= SIDE_MIN_M && v.width <= SIDE_MAX_M)) out.width = side
  if (!(v.depth >= SIDE_MIN_M && v.depth <= SIDE_MAX_M)) out.depth = side
  if (!Number.isFinite(v.east) || Math.abs(v.east) > OFFSET_MAX_M) out.east = `Enter at most ${OFFSET_MAX_M.toLocaleString('en-US')} m either way`
  if (!Number.isFinite(v.north) || Math.abs(v.north) > OFFSET_MAX_M) out.north = `Enter at most ${OFFSET_MAX_M.toLocaleString('en-US')} m either way`
  if (!Number.isFinite(v.heading)) out.heading = 'Enter a number of degrees'
  return out
}

/** The WGS84 boundary of the rectangle about the confirmed anchor. */
export function boundaryAround(anchor: LngLat, v: BoundaryInputs): LngLatTuple[] {
  return rectangleLocal({ x: v.east, y: v.north }, v.width, v.depth, v.heading)
    .map(p => fromLocal(anchor, p))
    .map(p => [p.lng, p.lat] as LngLatTuple)
}

const levelsOf = (b: ContextBuilding): number | undefined => {
  const n = Number(b.tags['building:levels'])
  return Number.isFinite(n) && n > 0 ? n : undefined
}

/**
 * The cached OSM buildings whose centroid lies inside the Site boundary, as
 * proposal sources. A tagged height is sent as height; levels as levels; any
 * other (landuse or default) height is left out, so the backend records
 * `fallback` evidence instead of a guess dressed as a measurement.
 */
export function osmSourcesInside(site: Site, buildings: ContextBuilding[]): SourceInput[] {
  const poly = site.boundary.map(([lng, lat]) => toLocal(site.origin, { lng, lat }))
  const out: SourceInput[] = []
  for (const b of buildings) {
    const ring = b.polygon.map(([lng, lat]) => toLocal(site.origin, { lng, lat }))
    if (ring.length < 3) continue
    const c = { x: ring.reduce((s, p) => s + p.x, 0) / ring.length, y: ring.reduce((s, p) => s + p.y, 0) / ring.length }
    if (!pointInPolygonLocal(c, poly)) continue
    const src: SourceInput = { namespace: 'osm', source_id: String(b.id), ring: b.polygon.map(([lng, lat]) => [lng, lat]) }
    if (b.height_source === 'tag') src.height_m = b.height_m
    else if (b.height_source === 'levels' && levelsOf(b) !== undefined) src.levels = levelsOf(b)
    out.push(src)
  }
  return out
}

/** The OSM ids a scene or preview already draws, so the context layer does not draw them twice. */
export function sceneOsmIds(buildings: readonly SceneBuilding[]): Set<number> {
  const ids = new Set<number>()
  for (const b of buildings) if (b.source.namespace === 'osm') ids.add(Number(b.source.source_id))
  return ids
}

/** A building's greenfield parameters, read back from its footprint (for the correction controls). */
export function greenfieldFromBuilding(b: SceneBuilding): GreenfieldInput | null {
  if (b.source.namespace !== 'greenfield' || b.footprint.length !== 4) return null
  const [p0, p1, p2] = b.footprint
  const cx = b.footprint.reduce((s, p) => s + p[0], 0) / 4
  const cy = b.footprint.reduce((s, p) => s + p[1], 0) / 4
  // Any edge is a valid "right" axis: (heading, width, depth) and
  // (heading + 90°, depth, width) are the same rectangle. right = (cos h, −sin h).
  const right = [p1[0] - p0[0], p1[1] - p0[1]]
  const width = Math.hypot(right[0], right[1])
  const depth = Math.hypot(p2[0] - p1[0], p2[1] - p1[1])
  const heading = ((Math.atan2(-right[1], right[0]) * 180) / Math.PI + 360) % 360
  const r = (v: number) => Math.round(v * 100) / 100
  return { id: b.source.source_id, center: [r(cx), r(cy)], width: r(width), depth: r(depth), height: r(b.height_m), heading: r(heading), unit: 'm' }
}

export const HEIGHT_MAX_M = 1000
export const HEIGHT_EVIDENCE_WORDS: Record<SceneBuilding['height_evidence'], string> = {
  tagged: 'height from its tag', levels: 'height from its levels', fallback: 'height not mapped: 6 m assumed',
  greenfield: 'proposed building',
}
