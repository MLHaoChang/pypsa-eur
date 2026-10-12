// The `sites.json` document — the 3D site view's own sidecar, mirrored from
// backend/services/site_service.py. Keep the two in step; the backend
// validates on PUT and the store validates the fields the UI can produce.

export type LngLatTuple = [number, number]

export interface Placement {
  /** Metres east of the site origin. */
  x: number
  /** Metres north of the site origin. */
  y: number
  /** Degrees clockwise from north. */
  heading: number
}

export interface Site {
  id: string
  name: string
  buses: string[]
  /** WGS84 polygon, ≥ 3 vertices, implicitly closed. */
  boundary: LngLatTuple[]
  /** Stored at creation (the boundary centroid then); placements are relative to it. */
  origin: { lng: number; lat: number }
  /** Keyed `"<Class>:<name>"`, e.g. `"Generator:Gas gensets"`. */
  placements: Record<string, Placement>
  context_fetched_at?: string
}

export interface SitesDocument {
  version: 1
  sites: Site[]
}

export const SITE_ID_RE = /^[A-Za-z0-9_-]{1,64}$/

export const PLACEABLE_CLASSES = new Set(['Bus', 'Generator', 'StorageUnit', 'Store', 'Load', 'Transformer', 'Line', 'Link'])

export const placementKey = (cls: string, name: string): string => `${cls}:${name}`

export const emptySitesDocument = (): SitesDocument => ({ version: 1, sites: [] })

// ── Site context (WP5): OSM footprints + terrain, mirrored from
// backend/services/site_context.py (`context.json`, schema version 1). ──────

export type ContextLineKind = 'road' | 'rail' | 'fence' | 'power_line'
export type ContextAreaKind = 'power_substation' | 'landuse' | 'water'

export interface ContextBuilding {
  id: number
  /** WGS84 ring, implicitly closed. */
  polygon: LngLatTuple[]
  height_m: number
  height_source: 'tag' | 'levels' | 'landuse' | 'default'
  tags: Record<string, string>
}

export interface ContextLine {
  id: number
  kind: ContextLineKind
  points: LngLatTuple[]
  tags: Record<string, string>
}

export interface ContextArea {
  id: number
  kind: ContextAreaKind
  polygon: LngLatTuple[]
  tags: Record<string, string>
}

export interface TerrainGrid {
  z: number
  /** Samples per side; `heights_m` has grid² entries, row 0 = north, column 0 = west. */
  grid: number
  /** [min_lng, min_lat, max_lng, max_lat] the samples span (row/column 0 on the max_lat / min_lng edge). */
  bbox: [number, number, number, number]
  heights_m: number[]
  source: string
  missing_tiles: number
}

export interface SiteContext {
  version: 1
  source: string
  fetched_at: string
  bbox: [number, number, number, number]
  buildings: ContextBuilding[]
  lines: ContextLine[]
  areas: ContextArea[]
  terrain: TerrainGrid | null
  attribution: string[]
}
