// Bus coordinates, one source of truth. PyPSA's convention is bus.x =
// longitude, bus.y = latitude; Leaflet wants [lat, lng] tuples, so every
// consumer needs the same swap and the same validity rule.
//
// WHY (0, 0) MEANS "NOT PLACED". PyPSA's Bus.x and Bus.y default to 0.0, and a
// netCDF export omits all-default columns entirely — a network whose author
// never set coordinates arrives with no x/y variables at all. Treating that as
// a real position put every such bus at 0°N 0°E in the Gulf of Guinea, made
// the map fit to a zero-size bounds and slam to maximum zoom over open water
// where Esri has no imagery, and suppressed the map's own "no coordinates"
// warning because zero buses looked missing. It also let the backend's
// haversine measure line lengths TO Null Island and write them into the model.
// See docs/superpowers/specs/2026-07-30-unplaced-buses-map-design.md.
//
// The rule is BOTH coordinates exactly zero. A bus at (0, 51.5) is Greenwich
// and stays valid; `x === 0 || y === 0` would hide it. `-0.0 === 0` in
// JavaScript, so negative zero needs no separate case.
//
// This module is deliberately pure — no React, no Leaflet — so the rule can be
// tested directly and imported from both the map and any future consumer.
// `utils/carriers.ts` exists because the same kind of predicate was
// copy-pasted into four components and drifted; this is that lesson applied
// before the drift rather than after.

export interface BusCoords {
  x: number | null | undefined
  y: number | null | undefined
}

/** Leaflet-ordered [lat, lng], or null when the bus has no usable location. */
export function busLatLng(b: BusCoords): [number, number] | null {
  // Explicit null/undefined check BEFORE the numeric coercion below.
  // `Number(null) === 0`, so without this a bus missing only ONE coordinate
  // (x null, y set, or vice versa) would coerce the missing half to 0 and
  // render on a fabricated meridian/equator instead of being treated as
  // unplaced. `== null` catches both `null` and `undefined` in one check.
  // Reachable in practice: a NaN coordinate serialises to JSON `null`
  // (backend `services/serialization.py`'s `clean_scalar`), so this isn't a
  // hypothetical shape.
  if (b.x == null || b.y == null) return null
  const lat = Number(b.y)
  const lng = Number(b.x)
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null
  if (lat === 0 && lng === 0) return null
  if (lat < -90 || lat > 90 || lng < -180 || lng > 180) return null
  return [lat, lng]
}

export function isPlaced(b: BusCoords): boolean {
  return busLatLng(b) !== null
}

/** Names of the buses still awaiting a location, in the order given. */
export function unplacedBusNames<T extends BusCoords & { name: string }>(buses: T[]): string[] {
  return buses.filter(b => !isPlaced(b)).map(b => b.name)
}

// ── Lengths from geometry (plan M2) ──────────────────────────────────────────
// The frontend twin of backend/services/network_geometry.py's
// `_haversine_km` / `route_length_km`, in the map's [lat, lng] order (what
// `busLatLng` returns and what `EditableLine` holds), so the discrepancy badge
// can be computed client-side without a round trip. Same sphere radius, same
// formula; both are tested against the same geodesics (one degree of latitude
// ≈ 111.19 km, a two-leg route is the sum of its legs).

const EARTH_KM = 6371.0

/** Great-circle distance between two [lat, lng] points, in km. */
export function haversineKm(a: readonly [number, number], b: readonly [number, number]): number {
  const toRad = (d: number) => (d * Math.PI) / 180
  const phi1 = toRad(a[0]), phi2 = toRad(b[0])
  const dPhi = toRad(b[0] - a[0]), dLam = toRad(b[1] - a[1])
  const h = Math.sin(dPhi / 2) ** 2 + Math.cos(phi1) * Math.cos(phi2) * Math.sin(dLam / 2) ** 2
  return 2 * EARTH_KM * Math.asin(Math.min(1, Math.sqrt(h)))
}

/**
 * Geodesic length of a polyline of [lat, lng] points — bus0, the interior
 * waypoints, bus1 — haversine per segment. Fewer than two points is 0.
 */
export function routeLengthKm(points: readonly (readonly [number, number])[]): number {
  let total = 0
  for (let i = 1; i < points.length; i++) total += haversineKm(points[i - 1], points[i])
  return total
}

/**
 * The discrepancy badge's thresholds: a stored length disagrees with its
 * geometry when the two differ by more than 10 % of the geometry OR 100 m,
 * whichever is LARGER — so a 50 m campus feeder is not flagged over a 6 m
 * drag, and a 400 km line is not flagged over 100 m of waypoint jitter.
 */
export const LENGTH_DISCREPANCY_REL = 0.10
export const LENGTH_DISCREPANCY_ABS_KM = 0.1

/** Does `storedKm` disagree with `geometryKm` beyond the thresholds? Non-finite inputs never flag. */
export function lengthDisagrees(storedKm: number | null | undefined, geometryKm: number): boolean {
  if (storedKm == null || !Number.isFinite(storedKm) || !Number.isFinite(geometryKm)) return false
  const tolerance = Math.max(LENGTH_DISCREPANCY_REL * geometryKm, LENGTH_DISCREPANCY_ABS_KM)
  return Math.abs(storedKm - geometryKm) > tolerance
}

/** "2.4 km" / "340 m" / "1,250 km" — one formatter for tooltips and the badge. */
export function fmtKm(km: number): string {
  if (!Number.isFinite(km)) return '—'
  if (km < 1) return `${Math.round(km * 1000)} m`
  if (km < 100) return `${km.toFixed(1)} km`
  return `${Math.round(km).toLocaleString('en-US')} km`
}
