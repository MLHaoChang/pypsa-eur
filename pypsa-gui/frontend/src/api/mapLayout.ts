import { client } from './client'

// The map view's sidecar (`map_layout.json`), a project sub-resource beside
// `/layout`, mirrored from backend/services/map_layout_service.py. Keep the
// two in step: the backend validates on PUT (422 names the field), caps (413)
// and is lock-checked (409 `project_locked`, quiet-toasted). GET degrades to
// the empty document.
//
// `points` are a branch's INTERIOR waypoints in GeoJSON `[lng, lat]` order —
// the ends are the buses and are not stored, so a bus drag keeps the interior
// intact. The map itself speaks Leaflet `[lat, lng]`; `pages/mapLayoutStore`
// converts at its boundary. Route keys are the map's edge ids, `<kind>:<name>`
// with kind `line` | `link` | `tr`; bubble keys are `<bus>|<category>`.

export type LngLatTuple = [number, number]
export type RouteSource = 'user' | 'import' | 'osm'
/**
 * Where a branch's PyPSA `length` came from (plan M2): a number the user or
 * an import typed, the bus0→bus1 chord, or the geodesic length of its route.
 * PyPSA has no column for this, so the document carries it. A branch with no
 * entry is `typed` — unknown, and nothing was ever derived for it.
 */
export type LengthSource = 'typed' | 'chord' | 'route'

export interface MapRoute {
  points: LngLatTuple[]
  source: RouteSource
}

export interface MapLengthProvenance {
  source: LengthSource
}

export interface MapBubble {
  /** Pixel offset from the bus marker, east. */
  dx: number
  /** Pixel offset from the bus marker, south (screen y). */
  dy: number
}

export interface MapLayoutDocument {
  version: 1
  routes: Record<string, MapRoute>
  bubbles: Record<string, MapBubble>
  /** Optional: absent on every pre-M2 document; keyed like `routes`. */
  lengths?: Record<string, MapLengthProvenance>
}

/** The provenance of one branch's length; `typed` when the document has no entry. */
export const lengthSourceOf = (doc: MapLayoutDocument, edgeId: string): LengthSource =>
  doc.lengths?.[edgeId]?.source ?? 'typed'

export const emptyMapLayoutDocument = (): MapLayoutDocument => ({ version: 1, routes: {}, bubbles: {} })

const isRecord = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === 'object' && !Array.isArray(v)

/**
 * Coerce whatever the wire (or localStorage) holds into a document, or the
 * empty one. Defensive like `coercePersistedState`: a future format bump
 * must degrade to "no routes" (the chords still draw), never crash the map.
 */
export function coerceMapLayoutDocument(raw: unknown): MapLayoutDocument {
  if (!isRecord(raw) || raw.version !== 1 || !isRecord(raw.routes) || !isRecord(raw.bubbles)) {
    return emptyMapLayoutDocument()
  }
  return raw as unknown as MapLayoutDocument
}

const url = (project: string) => `/projects/${encodeURIComponent(project)}/map_layout`

export const mapLayoutApi = {
  getMapLayout: (project: string): Promise<MapLayoutDocument> =>
    client.get<unknown>(url(project)).then(r => coerceMapLayoutDocument(r.data)),
  putMapLayout: (project: string, doc: MapLayoutDocument) =>
    client.put<{ saved: string; routes: number; bubbles: number }>(url(project), doc).then(r => r.data),
}
