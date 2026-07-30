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
