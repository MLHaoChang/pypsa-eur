// Geometry for the 3D site view: a local tangent plane around one bus, and
// the Web Mercator tile arithmetic that turns the Esri imagery already used by
// the map view into a ground texture.
//
// Pure on purpose — no React, no three.js, no DOM — so the arithmetic is unit
// tested directly (see geo.test.ts) and the canvas only composes it. The same
// rule utils/geo.ts follows for the map's [lat, lng] swap.
//
// Frame conventions. The scene is a right-handed ENU frame at the site
// origin: local `x` is metres EAST, local `y` is metres NORTH, `z` is UP.
// three.js is Y-up, so the canvas maps (east, north, up) → (x, -z, y); that
// mapping lives in ONE place (SiteCanvas's `toScene`) and nowhere here.
//
// Why a tangent plane and not Mercator metres. At a ~1 km site the equirect-
// angular approximation (metres per degree scaled by cos(lat)) is accurate to
// well under a metre, which is finer than any footprint we place; Mercator
// metres would be off by 1/cos(lat) — a factor 1.6 at 52°N — and would put a
// 20 ft container at 32 ft. Tiles ARE Mercator, so the ground mosaic's corners
// are converted through lon/lat, never through tile-pixel metres.

export interface LngLat { lng: number; lat: number }
export interface LocalXY { x: number; y: number }

const EARTH_RADIUS_M = 6378137
const DEG = Math.PI / 180

/** Metres per degree of latitude / longitude at a given latitude. */
export function metresPerDegree(lat: number): { lat: number; lng: number } {
  const perLat = EARTH_RADIUS_M * DEG
  return { lat: perLat, lng: perLat * Math.cos(lat * DEG) }
}

/** Project a lon/lat onto the local tangent plane at `origin` (east, north in metres). */
export function toLocal(origin: LngLat, p: LngLat): LocalXY {
  const m = metresPerDegree(origin.lat)
  return { x: (p.lng - origin.lng) * m.lng, y: (p.lat - origin.lat) * m.lat }
}

/** Inverse of `toLocal`. */
export function fromLocal(origin: LngLat, p: LocalXY): LngLat {
  const m = metresPerDegree(origin.lat)
  return { lng: origin.lng + p.x / m.lng, lat: origin.lat + p.y / m.lat }
}

// ── Web Mercator tiles (the XYZ scheme Esri, OSM and MapTiler all use) ──────

/** Fractional tile coordinates of a lon/lat at zoom `z`. */
export function lngLatToTile(p: LngLat, z: number): { x: number; y: number } {
  const n = 2 ** z
  const latR = p.lat * DEG
  return {
    x: ((p.lng + 180) / 360) * n,
    y: ((1 - Math.log(Math.tan(latR) + 1 / Math.cos(latR)) / Math.PI) / 2) * n,
  }
}

/** Lon/lat of the north-west corner of tile (x, y) at zoom `z`. */
export function tileToLngLat(x: number, y: number, z: number): LngLat {
  const n = 2 ** z
  const lng = (x / n) * 360 - 180
  const latR = Math.atan(Math.sinh(Math.PI * (1 - (2 * y) / n)))
  return { lng, lat: latR / DEG }
}

export interface TileRange { z: number; x0: number; x1: number; y0: number; y1: number }

/**
 * The inclusive tile range whose mosaic covers a square of `halfSizeM` metres
 * around `centre` at zoom `z`. The mosaic is always at least the requested
 * square and usually a little larger — the ground plane is sized to the
 * mosaic (see `mosaicExtent`), so the texture never stretches.
 */
export function tileRangeAround(centre: LngLat, halfSizeM: number, z: number): TileRange {
  const m = metresPerDegree(centre.lat)
  const dLng = halfSizeM / m.lng
  const dLat = halfSizeM / m.lat
  const nw = lngLatToTile({ lng: centre.lng - dLng, lat: centre.lat + dLat }, z)
  const se = lngLatToTile({ lng: centre.lng + dLng, lat: centre.lat - dLat }, z)
  const max = 2 ** z - 1
  const clamp = (v: number) => Math.min(max, Math.max(0, Math.floor(v)))
  return { z, x0: clamp(nw.x), x1: clamp(se.x), y0: clamp(nw.y), y1: clamp(se.y) }
}

export function tileCount(r: TileRange): number {
  return (r.x1 - r.x0 + 1) * (r.y1 - r.y0 + 1)
}

export interface LocalExtent { west: number; east: number; south: number; north: number }

/**
 * The mosaic's footprint on the local tangent plane, from its lon/lat corners.
 * Mercator tiles are not perfectly rectangular in metres, but across a ~1 km
 * mosaic the trapezoid error is centimetres, so a rectangle is used.
 */
export function mosaicExtent(origin: LngLat, r: TileRange): LocalExtent {
  const nw = toLocal(origin, tileToLngLat(r.x0, r.y0, r.z))
  const se = toLocal(origin, tileToLngLat(r.x1 + 1, r.y1 + 1, r.z))
  return { west: nw.x, east: se.x, south: se.y, north: nw.y }
}

/**
 * Pick the zoom at which one tile is roughly `targetTiles` across the square,
 * capped at `maxZoom` (Esri World Imagery serves to 19 nearly everywhere).
 * A ~1 km site at z17 is ~4×4 tiles of ~300 m — sharp enough to read fences
 * and roads, cheap enough (≤16 requests) to fetch on every site switch.
 */
export function zoomFor(centreLat: number, halfSizeM: number, targetTiles = 4, maxZoom = 19): number {
  const sizeM = halfSizeM * 2
  // Ground resolution of one tile at zoom z: 2πR·cos(lat) / 2^z metres.
  const circumference = 2 * Math.PI * EARTH_RADIUS_M * Math.cos(centreLat * DEG)
  const z = Math.log2((circumference * targetTiles) / sizeM)
  return Math.max(1, Math.min(maxZoom, Math.round(z)))
}
