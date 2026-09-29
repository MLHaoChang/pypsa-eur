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
