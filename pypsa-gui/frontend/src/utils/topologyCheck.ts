// The topology check (visual-layers plan 2, M5): what the model and the
// map's geometry say about each other, computed client-side and shown as a
// group in the Issues panel beside the 3D view's "Site layout" findings.
// Advisory — nothing here blocks a save or a solve.
//
// Five rules, each over the model (`buses`, branches), `map_layout.json`'s
// routes (interior waypoints, in the map's [lat, lng]) and `sites.json`'s
// boundaries. Pure: no React, no Leaflet, no store. The thresholds are
// exported so the tests pin them and the messages can name them.

import { busLatLng, fmtKm, haversineKm, lengthDisagrees, routeLengthKm, type BusCoords } from './geo'
import { pointInBoundary } from '../site3d/boundary'
import type { LngLatTuple } from '../site3d/types'

/** Two placed buses closer than this are probably one bus placed twice. */
export const NEAR_BUSES_M = 5
/** A transformer's two buses further apart than this are not one substation. */
export const TRANSFORMER_SPAN_M = 500

export type TopologyFindingKind = 'unplaced' | 'nearBuses' | 'transformerSpan' | 'lengthMismatch' | 'routeLeavesSite'

export interface TopologyFinding {
  kind: TopologyFindingKind
  severity: 'warn' | 'info'
  /** The component the row deep-links to. */
  component: { type: 'Bus' | 'Line' | 'Link' | 'Transformer'; name: string }
  /** The other party: the second bus of a pair, the site a route leaves. */
  other?: string
  /** Written for the user: names, metres, kilometres. */
  message: string
}

export interface TopologyBus extends BusCoords { name: string }
export interface TopologyBranch { name: string; bus0: string; bus1: string; length?: number }
export interface TopologySite { id: string; name: string; boundary: LngLatTuple[] }

export interface TopologyInput {
  buses: readonly TopologyBus[]
  lines: readonly TopologyBranch[]
  links: readonly TopologyBranch[]
  transformers: readonly TopologyBranch[]
  /** `<kind>:<name>` → interior waypoints in [lat, lng] (the map layout store's `routeWaypoints`). */
  routes: Readonly<Record<string, readonly (readonly [number, number])[]>>
  sites: readonly TopologySite[]
}

export const TOPOLOGY_FINDING_TITLES: Record<TopologyFindingKind, string> = {
  unplaced: 'Bus has no location',
  nearBuses: 'Two buses at the same spot',
  transformerSpan: 'Transformer buses far apart',
  lengthMismatch: 'Length disagrees with the map',
  routeLeavesSite: 'Route leaves its site',
}

const fmtM = (m: number): string => (m < 10 ? `${m.toFixed(1)} m` : `${Math.round(m)} m`)

export function topologyCheck(input: TopologyInput): TopologyFinding[] {
  const out: TopologyFinding[] = []
  const placed = new Map<string, [number, number]>()
  for (const b of input.buses) {
    const ll = busLatLng(b)
    if (ll) placed.set(b.name, ll)
    else out.push({ kind: 'unplaced', severity: 'info', component: { type: 'Bus', name: b.name }, message: `${b.name} has no location on the map (place it, or paste a lat, lng pair into its properties).` })
  }

  // Pairs within NEAR_BUSES_M: one row per pair, on the first bus.
  const names = [...placed.keys()]
  for (let i = 0; i < names.length; i++) {
    for (let j = i + 1; j < names.length; j++) {
      const m = haversineKm(placed.get(names[i])!, placed.get(names[j])!) * 1000
      if (m <= NEAR_BUSES_M) {
        out.push({ kind: 'nearBuses', severity: 'warn', component: { type: 'Bus', name: names[i] }, other: names[j], message: `${names[i]} and ${names[j]} are ${fmtM(m)} apart (under ${NEAR_BUSES_M} m): one bus placed twice, or two that should share a site?` })
      }
    }
  }

  for (const t of input.transformers) {
    const c0 = placed.get(t.bus0), c1 = placed.get(t.bus1)
    if (!c0 || !c1) continue
    const m = haversineKm(c0, c1) * 1000
    if (m > TRANSFORMER_SPAN_M) {
      out.push({ kind: 'transformerSpan', severity: 'warn', component: { type: 'Transformer', name: t.name }, message: `${t.name} joins ${t.bus0} and ${t.bus1}, ${fmtKm(m / 1000)} apart (over ${TRANSFORMER_SPAN_M} m): a transformer stands in one substation; move a bus or model the distance as a line.` })
    }
  }

  const geometryOf = (key: string, c0: [number, number], c1: [number, number]) => {
    const wps = input.routes[key] ?? []
    return { km: routeLengthKm([c0, ...wps, c1]), routed: wps.length > 0, wps }
  }
  const lengthRule = (type: 'Line' | 'Link', kind: 'line' | 'link', b: TopologyBranch) => {
    const c0 = placed.get(b.bus0), c1 = placed.get(b.bus1)
    if (!c0 || !c1) return
    if (b.length == null || !Number.isFinite(b.length) || (kind === 'link' && b.length <= 0)) return
    const g = geometryOf(`${kind}:${b.name}`, c0, c1)
    if (lengthDisagrees(b.length, g.km)) {
      out.push({ kind: 'lengthMismatch', severity: 'warn', component: { type, name: b.name }, message: `${b.name} is stored as ${fmtKm(b.length)} but its ${g.routed ? 'route' : 'chord'} on the map measures ${fmtKm(g.km)}. Use the map's "Use geometry" badge, or correct the length.` })
    }
  }
  for (const l of input.lines) lengthRule('Line', 'line', l)
  for (const l of input.links) lengthRule('Link', 'link', l)

  // A routed branch whose BOTH buses sit inside a site, but whose route
  // leaves every such site: the cable is drawn over the fence. A branch to
  // a bus outside every site is a feeder and is expected to leave.
  const inside = (ll: [number, number], s: TopologySite) => pointInBoundary({ lng: ll[1], lat: ll[0] }, s.boundary)
  const routeRule = (type: 'Line' | 'Link' | 'Transformer', kind: string, b: TopologyBranch) => {
    const wps = input.routes[`${kind}:${b.name}`]
    if (!wps || wps.length === 0) return
    const c0 = placed.get(b.bus0), c1 = placed.get(b.bus1)
    if (!c0 || !c1) return
    const both = input.sites.filter(s => s.boundary.length >= 3 && inside(c0, s) && inside(c1, s))
    if (both.length === 0) return
    const kept = both.filter(s => wps.every(w => inside([w[0], w[1]], s)))
    if (kept.length > 0) return
    const site = both[0]
    out.push({ kind: 'routeLeavesSite', severity: 'info', component: { type, name: b.name }, other: site.name, message: `${b.name}'s route leaves site ${site.name} although both ${b.bus0} and ${b.bus1} are inside it; bend it back within the boundary or extend the site.` })
  }
  for (const l of input.lines) routeRule('Line', 'line', l)
  for (const l of input.links) routeRule('Link', 'link', l)
  for (const t of input.transformers) routeRule('Transformer', 'tr', t)
  return out
}
