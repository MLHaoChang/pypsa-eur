import { client } from './client'
import type { LengthsFromGeometryResult } from './network'

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

// ── GeoJSON / KML import (plan M3) ──────────────────────────────────────────
// `POST …/map_layout/import` answers the MATCH REPORT; nothing is written
// unless `apply` is true. Mirrors backend/services/map_geometry_import.py.

export interface ImportRouteMatch {
  feature: number
  label: string
  /** `<kind>:<name>` of the branch the feature matched. */
  key: string
  how: 'name' | 'endpoints'
  /** Interior `[lng, lat]` vertices that would be stored; empty = a chord (any route is removed). */
  points: LngLatTuple[]
  reversed: boolean
}
export interface ImportSiteMatch {
  feature: number
  id: string
  name: string
  boundary: LngLatTuple[]
  buses: string[]
  origin: { lng: number; lat: number }
}
export interface ImportUnmatched { feature: number; label: string; reason: string }
export interface ImportReport {
  routes: ImportRouteMatch[]
  sites: ImportSiteMatch[]
  unmatched: ImportUnmatched[]
  applied: boolean
  written?: { routes: string[]; sites: string[] }
  /** Present after an apply with "Derive lengths from geometry" on. */
  lengths?: LengthsFromGeometryResult
}

// ── OSM suggestions (plan M3) ───────────────────────────────────────────────
export interface OsmSuggestedWay { id: number; kind: 'line' | 'minor_line' | 'cable'; points: LngLatTuple[]; tags: Record<string, string> }
export interface OsmSuggestedArea { id: number; kind: 'substation'; polygon: LngLatTuple[]; tags: Record<string, string> }
export interface OsmSuggestions {
  bbox: [number, number, number, number]
  lines: OsmSuggestedWay[]
  areas: OsmSuggestedArea[]
  attribution: string
  cached: boolean
}

export const mapLayoutApi = {
  getMapLayout: (project: string): Promise<MapLayoutDocument> =>
    client.get<unknown>(url(project)).then(r => coerceMapLayoutDocument(r.data)),
  putMapLayout: (project: string, doc: MapLayoutDocument) =>
    client.put<{ saved: string; routes: number; bubbles: number }>(url(project), doc).then(r => r.data),
  /** Preview (`apply: false`) or apply a GeoJSON / KML file. The file goes up as multipart `file`. */
  importGeometry: (project: string, file: File, opts: { apply: boolean; toleranceM?: number }): Promise<ImportReport> => {
    const form = new FormData()
    form.append('file', file, file.name)
    const params: Record<string, string> = { apply: String(opts.apply) }
    if (opts.toleranceM != null) params.tolerance_m = String(opts.toleranceM)
    return client.post<ImportReport>(`${url(project)}/import`, form, { params, timeout: 120_000 }).then(r => r.data)
  },
  /** `bbox` is `[min_lng, min_lat, max_lng, max_lat]`. 429 / 504 carry the Overpass setting's name. */
  osmSuggestions: (project: string, bbox: [number, number, number, number]): Promise<OsmSuggestions> =>
    client.get<OsmSuggestions>(`${url(project)}/osm_suggestions`, { params: { bbox: bbox.join(',') }, skipErrorToast: true }).then(r => r.data),
}
