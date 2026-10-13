// OSM suggestions on the map (plan M3): which branch a suggested OSM way
// could be the route of, and the waypoints adopting it would store.
//
// Pure, in the map's [lat, lng] order so MapCanvas can hand it what it has.
// No Leaflet, no React. A suggestion is OSM's geometry; nothing is computed
// along roads — the user picks, and the way's vertices become the route.

import { haversineKm } from './geo'

export type LatLng = readonly [number, number]

export interface SuggestBranch {
  /** `<kind>:<name>`. */
  key: string
  label: string
  bus0: LatLng
  bus1: LatLng
}

export interface SuggestCandidate {
  key: string
  label: string
  /** The way's vertices in bus0→bus1 order — what `setRouteWaypoints` stores. */
  waypoints: LatLng[]
  /** Sum of the distances from the way's ends to the two buses, in metres. */
  gapM: number
}

/** A way's end may be this far from a bus and still be offered for the branch. */
export const SUGGEST_MAX_END_GAP_M = 500

/**
 * The branches a suggested way could route, nearest first: both ends of the
 * way within `maxEndGapM` of the branch's two buses in one orientation or
 * the other. The waypoints are oriented bus0→bus1. A way shorter than two
 * vertices offers nothing.
 */
export function suggestionCandidates(
  way: readonly LatLng[],
  branches: readonly SuggestBranch[],
  maxEndGapM: number = SUGGEST_MAX_END_GAP_M,
): SuggestCandidate[] {
  if (way.length < 2) return []
  const first = way[0], last = way[way.length - 1]
  const out: SuggestCandidate[] = []
  for (const b of branches) {
    const straight = (haversineKm(first, b.bus0) + haversineKm(last, b.bus1)) * 1000
    const flipped = (haversineKm(first, b.bus1) + haversineKm(last, b.bus0)) * 1000
    const reversed = flipped < straight
    const gapM = reversed ? flipped : straight
    const endA = reversed ? haversineKm(last, b.bus0) : haversineKm(first, b.bus0)
    const endB = reversed ? haversineKm(first, b.bus1) : haversineKm(last, b.bus1)
    if (endA * 1000 > maxEndGapM || endB * 1000 > maxEndGapM) continue
    out.push({ key: b.key, label: b.label, waypoints: (reversed ? [...way].reverse() : [...way]).map(p => [p[0], p[1]] as const), gapM })
  }
  return out.sort((a, b) => a.gapM - b.gapM)
}

/** What the OSM tags say a way is: "110 kV line", "cable", "minor line (Noordring)". */
export function describeWay(kind: string, tags: Record<string, string>): string {
  const base = kind === 'minor_line' ? 'minor line' : kind
  const voltage = tags.voltage ? `${formatVoltage(tags.voltage)} ` : ''
  const name = tags.name ? ` (${tags.name})` : ''
  return `${voltage}${base}${name}`
}

function formatVoltage(raw: string): string {
  const parts = raw.split(';').map(v => Number(v.trim())).filter(v => Number.isFinite(v) && v > 0)
  if (parts.length === 0) return raw
  return parts.map(v => (v >= 1000 ? `${Math.round(v / 1000)} kV` : `${v} V`)).join(' / ')
}
