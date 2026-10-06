// The map layout store — one `MapLayoutDocument` per project, memory-first.
//
// Persistence follows `topologyLayoutStore.ts` and the 3D branch's
// `sitesStore.ts` deliberately: a write lands in memory synchronously, a
// debounced PUT follows, `flushPendingMapLayoutToServer` forces it (the three
// save paths call it beside the layout flush), and localStorage is a FALLBACK
// for a failed PUT — never a source that could overwrite a real server
// document. A zustand store rather than component state because the document
// outlives MapCanvas (a blank↔map view switch unmounts it) and the save paths
// read it from outside React.
//
// Every write goes through `evaluateMutation`: a debounce armed before the
// project became read-only must not fire after it.
//
// Coordinates. The map (Leaflet) speaks `[lat, lng]`; the document (GeoJSON,
// backend/services/map_layout_service.py) speaks `[lng, lat]`. The conversion
// happens HERE and only here — `setRouteWaypoints` takes the map's tuples,
// `routeWaypoints` hands them back — so MapCanvas and `EditableLine` never see
// the document's order. `points` are a branch's INTERIOR waypoints; the ends
// are the buses, so a bus drag keeps the interior intact.
//
// Migration. Before M1 the map kept this state in two localStorage keys per
// project (`pypsa-gui:map:line-waypoints:<p>` as `[lat, lng]` keyed
// `<kind>:<name>`, `pypsa-gui:map:asset-offsets:<p>` keyed `<bus>::<cat>`).
// On the first load of a project whose SERVER document is empty, the legacy
// keys are read, the document is built from them and PUT, and the legacy keys
// are removed only once that PUT succeeded. A server that could not be reached
// is not an empty server, so nothing migrates then.

import { useEffect } from 'react'
import { create } from 'zustand'
import {
  emptyMapLayoutDocument, mapLayoutApi,
  type LngLatTuple, type MapBubble, type MapLayoutDocument, type MapRoute, type RouteSource,
} from '../api/mapLayout'
import { rawFetchHeaders } from '../api/csrf'
import { useUIStore } from '../store/uiStore'
import { evaluateMutation } from '../utils/mutationGuard'

export const MAP_LAYOUT_SAVE_DEBOUNCE_MS = 300

/** Leaflet's tuple order, what the map and `EditableLine` work in. */
export type LatLngTuple = [number, number]
export type EdgeKind = 'line' | 'link' | 'tr'

export const EDGE_KINDS: ReadonlySet<string> = new Set<EdgeKind>(['line', 'link', 'tr'])
export const edgeKey = (kind: EdgeKind, name: string): string => `${kind}:${name}`
export const bubbleKey = (bus: string, category: string): string => `${bus}|${category}`
/** PyPSA class → the map's edge-kind prefix; null for a class without a route. */
export const edgeKindForClass = (cls: string): EdgeKind | null =>
  cls === 'Line' ? 'line' : cls === 'Link' ? 'link' : cls === 'Transformer' ? 'tr' : null

export const localMapLayoutKey = (project: string | null): string => `pypsa-gui:map-layout:${project ?? '__local__'}`
export const legacyWaypointsKey = (project: string | null): string => `pypsa-gui:map:line-waypoints:${project ?? 'default'}`
export const legacyOffsetsKey = (project: string | null): string => `pypsa-gui:map:asset-offsets:${project ?? 'default'}`

// ── coordinate order ────────────────────────────────────────────────────────

export const toLngLat = (wps: readonly LatLngTuple[]): LngLatTuple[] => wps.map(([lat, lng]) => [lng, lat])
export const toLatLng = (pts: readonly LngLatTuple[]): LatLngTuple[] => pts.map(([lng, lat]) => [lat, lng])

/** The map's waypoint table (`<kind>:<name>` → `[lat, lng][]`) from a document. Memoise on the document. */
export function routeWaypoints(doc: MapLayoutDocument): Record<string, LatLngTuple[]> {
  const out: Record<string, LatLngTuple[]> = {}
  for (const [id, route] of Object.entries(doc.routes)) out[id] = toLatLng(route.points)
  return out
}

// ── validation of what the UI can produce (the backend re-validates) ────────

const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

export function assertEdgeIdValid(edgeId: string): void {
  const i = edgeId.indexOf(':')
  if (i <= 0 || i === edgeId.length - 1 || !EDGE_KINDS.has(edgeId.slice(0, i))) {
    throw new Error(`route id ${JSON.stringify(edgeId)} must be <line|link|tr>:<name>`)
  }
}

export function assertLatLngWaypointsValid(wps: readonly LatLngTuple[]): void {
  for (const w of wps) {
    if (!Array.isArray(w) || w.length !== 2 || !finite(w[0]) || !finite(w[1])) throw new Error('waypoint must be [lat, lng]')
    if (w[0] < -90 || w[0] > 90 || w[1] < -180 || w[1] > 180) throw new Error('waypoint out of range')
  }
}

export function assertBubbleValid(key: string, b: MapBubble): void {
  const i = key.lastIndexOf('|')
  if (i <= 0 || i === key.length - 1) throw new Error(`bubble key ${JSON.stringify(key)} must be <bus>|<category>`)
  if (!finite(b.dx) || !finite(b.dy)) throw new Error(`bubble ${key}: dx, dy must be finite`)
}

// ── localStorage: fallback slot + legacy keys ───────────────────────────────

function readLocal(project: string | null): MapLayoutDocument | null {
  try {
    const raw = localStorage.getItem(localMapLayoutKey(project))
    if (!raw) return null
    const parsed = JSON.parse(raw) as MapLayoutDocument
    return parsed && parsed.version === 1 && parsed.routes && parsed.bubbles ? parsed : null
  } catch { return null }
}
function writeLocal(project: string | null, doc: MapLayoutDocument): void {
  try { localStorage.setItem(localMapLayoutKey(project), JSON.stringify(doc)) } catch { /* quota: fallback only */ }
}
function clearLocal(project: string | null): void {
  try { localStorage.removeItem(localMapLayoutKey(project)) } catch { /* noop */ }
}

const isEmptyDoc = (doc: MapLayoutDocument): boolean =>
  Object.keys(doc.routes).length === 0 && Object.keys(doc.bubbles).length === 0

/**
 * The pre-M1 localStorage state as a document, or null when there is none
 * worth migrating. Entries that would fail validation are dropped rather than
 * failing the whole migration — a corrupt bubble must not strand every route.
 */
export function buildLegacyDocument(project: string | null): MapLayoutDocument | null {
  let wps: unknown = null, offs: unknown = null
  try { wps = JSON.parse(localStorage.getItem(legacyWaypointsKey(project)) ?? 'null') } catch { /* unusable */ }
  try { offs = JSON.parse(localStorage.getItem(legacyOffsetsKey(project)) ?? 'null') } catch { /* unusable */ }
  const doc = emptyMapLayoutDocument()
  if (wps && typeof wps === 'object' && !Array.isArray(wps)) {
    for (const [id, pts] of Object.entries(wps as Record<string, unknown>)) {
      if (!Array.isArray(pts) || pts.length === 0) continue
      try {
        assertEdgeIdValid(id)
        assertLatLngWaypointsValid(pts as LatLngTuple[])
      } catch { continue }
      doc.routes[id] = { points: toLngLat(pts as LatLngTuple[]), source: 'user' }
    }
  }
  if (offs && typeof offs === 'object' && !Array.isArray(offs)) {
    for (const [id, o] of Object.entries(offs as Record<string, unknown>)) {
      // Legacy bubble ids were `<bus>::<category>`; the document uses `|`.
      const sep = id.lastIndexOf('::')
      if (sep <= 0) continue
      const key = bubbleKey(id.slice(0, sep), id.slice(sep + 2))
      const b = o as MapBubble
      try { assertBubbleValid(key, { dx: b?.dx, dy: b?.dy }) } catch { continue }
      doc.bubbles[key] = { dx: b.dx, dy: b.dy }
    }
  }
  return isEmptyDoc(doc) ? null : doc
}

export function clearLegacyKeys(project: string | null): void {
  try {
    localStorage.removeItem(legacyWaypointsKey(project))
    localStorage.removeItem(legacyOffsetsKey(project))
  } catch { /* noop */ }
}

// ── the store ───────────────────────────────────────────────────────────────

interface MapLayoutState {
  docs: Record<string, MapLayoutDocument>
  loaded: Record<string, true>
  /** Projects with a write the server has not confirmed. */
  dirty: Record<string, true>
  docFor: (project: string | null) => MapLayoutDocument
  ensureLoaded: (project: string | null) => Promise<void>
  /**
   * Replace a branch's interior waypoints, given in the map's `[lat, lng]`
   * order. An empty list removes the route (a chord needs no entry).
   */
  setRouteWaypoints: (project: string | null, edgeId: string, wps: readonly LatLngTuple[], source?: RouteSource) => void
  setBubble: (project: string | null, key: string, bubble: MapBubble) => void
  /** Re-key `<kind>:old` → `<kind>:new` for a renamed Line / Link / Transformer; false when there was no route. */
  renameRoute: (project: string | null, cls: string, oldName: string, newName: string) => boolean
  /** Replace a project's document wholesale. */
  replaceDocument: (project: string | null, doc: MapLayoutDocument) => void
  resetForTests: () => void
}

const keyOf = (project: string | null): string => project ?? '__local__'
// One frozen instance: `docFor` is used as a selector, and a fresh object per
// call would re-render every subscriber on every store change.
const EMPTY_DOC: MapLayoutDocument = Object.freeze({
  version: 1, routes: Object.freeze({}) as Record<string, MapRoute>, bubbles: Object.freeze({}) as Record<string, MapBubble>,
}) as MapLayoutDocument
const timers = new Map<string, ReturnType<typeof setTimeout>>()

function canWrite(): boolean {
  const { readOnly, readOnlyReason } = useUIStore.getState()
  return evaluateMutation(readOnly, readOnlyReason).allowed
}

export const useMapLayoutStore = create<MapLayoutState>((set, get) => {
  // Mutate a project's document through `fn` (which returns the new
  // document), refuse when read-only, mark dirty, arm the debounce.
  const write = (project: string | null, fn: (doc: MapLayoutDocument) => MapLayoutDocument): void => {
    if (!canWrite()) return
    const k = keyOf(project)
    const next = fn(get().docFor(project))
    set(s => ({ docs: { ...s.docs, [k]: next }, dirty: { ...s.dirty, [k]: true } }))
    scheduleSave(project)
  }
  return {
    docs: {},
    loaded: {},
    dirty: {},
    docFor: project => get().docs[keyOf(project)] ?? EMPTY_DOC,
    ensureLoaded: async project => {
      const k = keyOf(project)
      if (get().loaded[k]) return
      let doc: MapLayoutDocument
      let serverAnswered = false
      if (!project) {
        doc = readLocal(null) ?? emptyMapLayoutDocument()
      } else {
        try {
          doc = await mapLayoutApi.getMapLayout(project)
          serverAnswered = true
        } catch {
          // Unreachable server: the local fallback is the best we have. A
          // server that ANSWERED (even with nothing) always wins.
          doc = readLocal(project) ?? emptyMapLayoutDocument()
        }
      }
      // One-time migration of the pre-M1 localStorage state. Only when the
      // authority for this project (the server, or the local slot for a
      // scratch network) holds nothing — never over a real document.
      let migrating: MapLayoutDocument | null = null
      if (isEmptyDoc(doc) && (serverAnswered || !project)) {
        migrating = buildLegacyDocument(project)
        if (migrating) doc = migrating
      }
      if (get().dirty[k]) {
        // A write that landed while the GET was in flight wins over the
        // fetch; the legacy keys stay for a later load to migrate.
        set(s => ({ loaded: { ...s.loaded, [k]: true } }))
        return
      }
      set(s => ({ docs: { ...s.docs, [k]: doc }, loaded: { ...s.loaded, [k]: true } }))
      if (migrating) await migrateLegacy(project)
    },
    setRouteWaypoints: (project, edgeId, wps, source = 'user') => {
      assertEdgeIdValid(edgeId)
      assertLatLngWaypointsValid(wps)
      write(project, doc => {
        const routes = { ...doc.routes }
        if (wps.length === 0) delete routes[edgeId]
        else routes[edgeId] = { ...routes[edgeId], points: toLngLat(wps), source }
        return { ...doc, routes }
      })
    },
    setBubble: (project, key, bubble) => {
      assertBubbleValid(key, bubble)
      write(project, doc => ({ ...doc, bubbles: { ...doc.bubbles, [key]: { dx: bubble.dx, dy: bubble.dy } } }))
    },
    renameRoute: (project, cls, oldName, newName) => {
      const kind = edgeKindForClass(cls)
      if (!kind) return false
      const oldKey = edgeKey(kind, oldName), newKey = edgeKey(kind, newName)
      if (!(oldKey in get().docFor(project).routes)) return false
      write(project, doc => {
        const { [oldKey]: moved, ...rest } = doc.routes
        return { ...doc, routes: { ...rest, [newKey]: moved } }
      })
      return true
    },
    replaceDocument: (project, doc) => write(project, () => doc),
    resetForTests: () => {
      for (const t of timers.values()) clearTimeout(t)
      timers.clear()
      set({ docs: {}, loaded: {}, dirty: {} })
    },
  }
})

// ── persistence ─────────────────────────────────────────────────────────────

function scheduleSave(project: string | null): void {
  const k = keyOf(project)
  const existing = timers.get(k)
  if (existing) clearTimeout(existing)
  timers.set(k, setTimeout(() => {
    timers.delete(k)
    void persist(project)
  }, MAP_LAYOUT_SAVE_DEBOUNCE_MS))
}

async function persist(project: string | null): Promise<'server' | 'local' | 'refused'> {
  if (!canWrite()) return 'refused'
  const doc = useMapLayoutStore.getState().docFor(project)
  if (!project) { writeLocal(null, doc); return 'local' }
  try {
    await mapLayoutApi.putMapLayout(project, doc)
    clearLocal(project)
    useMapLayoutStore.setState(s => {
      const dirty = { ...s.dirty }
      delete dirty[keyOf(project)]
      return { dirty }
    })
    return 'server'
  } catch (e) {
    writeLocal(project, doc)
    console.warn('[map-layout] PUT failed → wrote to localStorage instead', { project, error: e })
    return 'local'
  }
}

/**
 * Persist a document just built from the legacy keys. The legacy keys go
 * only once the authority holds the document: a successful PUT for a
 * project, the local slot for a scratch network. A failed or refused write
 * leaves them for the next load to try again (the document is dirty, so the
 * next flush retries too).
 */
async function migrateLegacy(project: string | null): Promise<void> {
  const k = keyOf(project)
  useMapLayoutStore.setState(s => ({ dirty: { ...s.dirty, [k]: true } }))
  const status = await persist(project)
  if (status === 'server' || (!project && status === 'local')) clearLegacyKeys(project)
}

export interface FlushMapLayoutResult {
  status: 'nothing' | 'server' | 'local' | 'refused'
  routes: number
  bubbles: number
}

/**
 * Force the pending write for `project` now. After a Save-As the caller
 * passes `previousProject` (the name the edits were made under): the
 * pending document is re-homed to the new name and PUT there — the twin of
 * `flushPendingLayoutToServer` chasing the `__local__` slot.
 */
export async function flushPendingMapLayoutToServer(
  project: string | null,
  opts: { previousProject?: string | null } = {},
): Promise<FlushMapLayoutResult> {
  const store = useMapLayoutStore.getState()
  const k = keyOf(project)
  let dirtyKey: string | null = store.dirty[k] ? k : null
  let movedFromScratch = false
  if (!dirtyKey && opts.previousProject !== undefined && opts.previousProject !== project) {
    const pk = keyOf(opts.previousProject)
    if (store.dirty[pk]) {
      // Re-home: the new project owns a COPY of the edited document. The
      // previous project's own pending write is left armed — a Save-a-Copy
      // keeps the user on the original, and its edits must still reach it.
      // Only a scratch network (no project) is a MOVE: it has nowhere else
      // to go, and its localStorage slot must not resurrect on the next
      // scratch network.
      const doc = store.docFor(opts.previousProject)
      movedFromScratch = opts.previousProject === null
      useMapLayoutStore.setState(s => {
        const dirty = { ...s.dirty, [k]: true as const }
        if (movedFromScratch) delete dirty[pk]
        return { docs: { ...s.docs, [k]: doc }, loaded: { ...s.loaded, [k]: true }, dirty }
      })
      if (movedFromScratch) {
        const t = timers.get(pk)
        if (t) { clearTimeout(t); timers.delete(pk) }
      }
      dirtyKey = k
    }
  }
  if (!dirtyKey) return { status: 'nothing', routes: 0, bubbles: 0 }
  const t = timers.get(dirtyKey)
  if (t) { clearTimeout(t); timers.delete(dirtyKey) }
  const status = await persist(project)
  if (status === 'server' && movedFromScratch) clearLocal(null)
  const doc = useMapLayoutStore.getState().docFor(project)
  return { status, routes: Object.keys(doc.routes).length, bubbles: Object.keys(doc.bubbles).length }
}

/** pagehide / beforeunload: localStorage first, then a keepalive PUT. */
export function persistMapLayoutOnUnload(project: string | null): void {
  const store = useMapLayoutStore.getState()
  const k = keyOf(project)
  if (!store.dirty[k] || !canWrite()) return
  const doc = store.docFor(project)
  writeLocal(project, doc)
  if (!project) return
  try {
    fetch(`/api/projects/${encodeURIComponent(project)}/map_layout`, {
      method: 'PUT',
      body: JSON.stringify(doc),
      headers: { 'Content-Type': 'application/json', ...rawFetchHeaders('PUT') },
      keepalive: true,
    }).catch(() => { /* localStorage fallback already done */ })
  } catch { /* fall through */ }
}

/**
 * MapCanvas lifecycle: load the active project's document, flush a pending
 * write when the canvas unmounts or the project changes (the debounce timer
 * is module-level, so this only brings the write forward), and persist on
 * pagehide/beforeunload, where an ordinary fetch would be cancelled.
 */
export function useMapLayoutLifecycle(project: string | null): void {
  const ensureLoaded = useMapLayoutStore(s => s.ensureLoaded)
  useEffect(() => {
    void ensureLoaded(project)
    return () => { void flushPendingMapLayoutToServer(project) }
  }, [project, ensureLoaded])
  useEffect(() => {
    const onHide = () => persistMapLayoutOnUnload(project)
    window.addEventListener('pagehide', onHide)
    window.addEventListener('beforeunload', onHide)
    return () => {
      window.removeEventListener('pagehide', onHide)
      window.removeEventListener('beforeunload', onHide)
    }
  }, [project])
}
