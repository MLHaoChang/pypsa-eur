// Site distances (visual-layers plan 3 S3, owner decision O1: placement
// carries meaning). For every branch (Line, Link, Transformer) whose two
// buses are members of the open site: how far its power travels, measured
// on the site.
//
// Two measures, one per branch:
//  - `route`: the project's map holds a route for the branch
//    (`map_layout.json`, keyed `line:` / `link:` / `tr:` as the map store
//    keys it). The route is measured as the map measures it — from the owner
//    bus's yard through the route's waypoints to the far bus's yard — on the
//    site's tangent plane (`geo.ts`), so the site's number IS the route's
//    length and the two views never disagree. The path is kept, in site
//    metres, for the flow chevrons to run along.
//  - `grid`: no route. The Manhattan distance between the branch object's
//    CONNECTION ANCHOR (the far-side end of its flow anchor, where the cable
//    leaves it) and the far yard, times the library's `ROUTING_FACTOR` —
//    cables on a campus run in orthogonal trenches. No path: the chevrons
//    stay on the object's own anchor.
//
// A branch whose far bus is not a member (its yard is not in the site) has
// no site distance. Yards themselves are measured by their origin.
//
// `siteRoutePoints` is the straight two-point route the site writes when a
// placement moves and the project derives lengths from geometry (plan M2's
// setting): the connection anchor and the far yard as the map's `[lat, lng]`
// tuples, so it plugs into `mapLayoutStore.setSiteRoute` as a user's route
// edit does. The backend then measures bus0 → anchor → far yard → bus1,
// which is what `route` measures here when the yards stand at their buses.
//
// Pure: no React, no three (main-bundle safe; the readout imports it). The
// map-store imports are the key helpers only.

import { fromLocal, toLocal, type LngLat } from './geo'
import { ROUTING_FACTOR } from './assetLibrary'
import { edgeKey, edgeKindForClass, type LatLngTuple } from '../pages/mapLayoutStore'
import { fmtKm } from '../utils/geo'
import type { LngLatTuple } from './types'
import type { Anchors } from './templates'

export type XY = [number, number]
export type V3 = [number, number, number]

/** What a distance needs of a layout object (a `SiteObject` has all of it). */
export interface DistanceObject {
  type: string
  name: string
  bus: string
  far?: string
  origin: XY
  heading: number
  anchors?: { flow?: { from: V3; to: V3 } }
}

export type DistanceSource = 'route' | 'grid'

export interface SiteDistance {
  metres: number
  source: DistanceSource
  /** The far bus (the yard measured to). */
  far: string
  /** Site metres, owner yard → waypoints → far yard; only for `route`. */
  path: XY[] | null
}

/** The map's route key for a branch object (`line:L1`, `link:K`, `tr:T1`); null for anything else. */
export function edgeKeyOf(o: Pick<DistanceObject, 'type' | 'name'>): string | null {
  const kind = edgeKindForClass(o.type)
  return kind ? edgeKey(kind, o.name) : null
}

/** A local (object-frame) point in the site frame: the object's rotation by its heading, then its origin. */
function toSite(o: Pick<DistanceObject, 'origin' | 'heading'>, p: readonly [number, number]): XY {
  // Clockwise-from-north heading is a NEGATIVE mathematical angle (east = +x, north = +y), as in fit.ts.
  const a = (-o.heading * Math.PI) / 180
  const cos = Math.cos(a), sin = Math.sin(a)
  return [o.origin[0] + p[0] * cos - p[1] * sin, o.origin[1] + p[0] * sin + p[1] * cos]
}

/** The inverse of `toSite`: a site point in the object's frame (east, north of its origin before its heading). */
function toObject(o: Pick<DistanceObject, 'origin' | 'heading'>, p: XY): XY {
  const a = (-o.heading * Math.PI) / 180
  const cos = Math.cos(a), sin = Math.sin(a)
  const dx = p[0] - o.origin[0], dy = p[1] - o.origin[1]
  return [dx * cos + dy * sin, -dx * sin + dy * cos]
}

/**
 * Where the cable leaves a branch object, in site metres: the far-side end
 * of its flow anchor (templates draw it owner side → far side), else its
 * origin. A yard's anchor is its origin.
 */
export function connectionAnchor(o: DistanceObject): XY {
  const flow = o.anchors?.flow
  return flow ? toSite(o, [flow.to[0], flow.to[1]]) : [o.origin[0], o.origin[1]]
}

const polylineLength = (pts: readonly XY[]): number => {
  let total = 0
  for (let i = 1; i < pts.length; i++) total += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
  return total
}

/** The member yards by bus name. */
export function yardsOf<T extends DistanceObject>(objects: readonly T[]): Map<string, T> {
  return new Map(objects.filter(o => o.type === 'Bus').map(o => [o.name, o]))
}

/** The branch objects with a far bus whose yard is in the site, with that yard. */
function memberBranches<T extends DistanceObject>(objects: readonly T[]): Array<{ o: T; owner: T; far: T; key: string }> {
  const yards = yardsOf(objects)
  const out: Array<{ o: T; owner: T; far: T; key: string }> = []
  for (const o of objects) {
    const key = edgeKeyOf(o)
    const owner = yards.get(o.bus), far = o.far ? yards.get(o.far) : undefined
    if (key && owner && far) out.push({ o, owner, far, key })
  }
  return out
}

/**
 * Every member branch's site distance, keyed as the map keys routes.
 * `routes` is the map document's table (`<kind>:<name>` → interior
 * `[lng, lat]` waypoints; the ends are the buses). Deterministic, computed
 * once per layout: nothing here runs per frame.
 */
export function siteDistances(
  objects: readonly DistanceObject[],
  routes: Readonly<Record<string, { points: readonly LngLatTuple[] }>>,
  siteOrigin: LngLat,
): Map<string, SiteDistance> {
  const out = new Map<string, SiteDistance>()
  for (const { o, owner, far, key } of memberBranches(objects)) {
    const waypoints = routes[key]?.points
    if (waypoints && waypoints.length > 0) {
      const via = waypoints.map(([lng, lat]) => { const p = toLocal(siteOrigin, { lng, lat }); return [p.x, p.y] as XY })
      const path: XY[] = [[owner.origin[0], owner.origin[1]], ...via, [far.origin[0], far.origin[1]]]
      out.set(key, { metres: polylineLength(path), source: 'route', far: far.name, path })
    } else {
      const a = connectionAnchor(o)
      const manhattan = Math.abs(far.origin[0] - a[0]) + Math.abs(far.origin[1] - a[1])
      out.set(key, { metres: manhattan * ROUTING_FACTOR, source: 'grid', far: far.name, path: null })
    }
  }
  return out
}

/**
 * The straight two-point route the site writes for a branch — its
 * connection anchor and its far yard — as the map store's `[lat, lng]`
 * tuples. Null when the far bus has no yard here.
 */
export function siteRoutePoints(o: DistanceObject, yards: ReadonlyMap<string, DistanceObject>, siteOrigin: LngLat): LatLngTuple[] | null {
  const far = o.far ? yards.get(o.far) : undefined
  if (!far) return null
  const a = fromLocal(siteOrigin, { x: connectionAnchor(o)[0], y: connectionAnchor(o)[1] })
  const b = fromLocal(siteOrigin, { x: far.origin[0], y: far.origin[1] })
  return [[a.lat, a.lng], [b.lat, b.lng]]
}

/**
 * The member branches a placement change touched: the branch itself, or the
 * yard at either end (`moved` holds placement keys, `"<Class>:<name>"`).
 */
export function affectedBranches<T extends DistanceObject>(objects: readonly T[], moved: ReadonlySet<string>): T[] {
  return memberBranches(objects)
    .filter(({ o, owner, far }) => moved.has(`${o.type}:${o.name}`) || moved.has(`Bus:${owner.name}`) || moved.has(`Bus:${far.name}`))
    .map(({ o }) => o)
}

/** "86 m" / "1.2 km" — the map's formatter, so the two views read alike. */
export const fmtMetres = (metres: number): string => fmtKm(metres / 1000)

/** The flow-path label: "TR1 → Campus 33 kV · 86 m". */
export function distanceLabel(o: Pick<DistanceObject, 'name'>, d: SiteDistance): string {
  return `${o.name} → ${d.far} · ${fmtMetres(d.metres)}`
}

/** One line on how the number was measured, for a title attribute. */
export function distanceTitle(d: SiteDistance): string {
  return d.source === 'route'
    ? 'Along the route drawn on the map, yard to yard'
    : `Manhattan distance from the connection anchor to the far yard × ${ROUTING_FACTOR} routing factor (no route drawn)`
}

/**
 * The object's flow anchor run along a site-metre path instead of its own
 * short segment: `from`, `via` and `to` in the OBJECT's frame (the results
 * layer adds them to the object's group), at the anchor's own height. The
 * object's anchors unchanged when it has no flow anchor or the path is null.
 */
export function flowAnchorsAlong(o: DistanceObject & { anchors: Anchors }, path: XY[] | null): Anchors {
  const flow = o.anchors.flow
  if (!flow || !path || path.length < 2) return o.anchors
  const z = flow.to[2]
  const local = path.map(p => { const [x, y] = toObject(o, p); return [x, y, z] as V3 })
  return { ...o.anchors, flow: { from: local[0], via: local.slice(1, -1), to: local[local.length - 1] } }
}
