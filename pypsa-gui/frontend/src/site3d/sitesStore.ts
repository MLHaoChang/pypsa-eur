// The sites document store — one `SitesDocument` per project, memory-first.
//
// Persistence follows `pages/topologyLayoutStore.ts` deliberately: a write
// lands in memory synchronously, a debounced PUT follows, `flushPendingSitesToServer`
// forces it (the three save paths call it beside the layout flush), and
// localStorage is a FALLBACK for a failed PUT — never a source that could
// overwrite a real server document. It is a zustand store rather than a bare
// Map because four surfaces read it (the map's polygons, the 3D canvas, the
// creation form's bus list, the site draft panel) and `useSyncExternalStore`
// semantics come for free.
//
// Every write goes through `evaluateMutation`: a debounce armed before the
// project became read-only must not fire after it.
//
// No `three` import here — this module is in the main bundle.

import { useEffect } from 'react'
import { create } from 'zustand'
import { sitesApi } from '../api/sites'
import { rawFetchHeaders } from '../api/csrf'
import { useUIStore } from '../store/uiStore'
import { evaluateMutation } from '../utils/mutationGuard'
import {
  PLACEABLE_CLASSES, SITE_ID_RE, emptySitesDocument, placementKey,
  type LngLatTuple, type Placement, type Site, type SitesDocument,
} from './types'

export const SITES_SAVE_DEBOUNCE_MS = 300

export const localSitesKey = (project: string | null): string => `pypsa-gui:sites:${project ?? '__local__'}`

// ── validation of what the UI can produce (the backend re-validates) ────────

export function assertSiteValid(site: Site): void {
  if (!SITE_ID_RE.test(site.id)) throw new Error(`site id ${JSON.stringify(site.id)} is not a valid id`)
  if (typeof site.name !== 'string') throw new Error('site name must be a string')
  assertBoundaryValid(site.boundary)
  if (!Number.isFinite(site.origin?.lng) || !Number.isFinite(site.origin?.lat)) throw new Error('site origin must be {lng, lat}')
  for (const [key, p] of Object.entries(site.placements ?? {})) assertPlacementValid(key, p)
}

export function assertBoundaryValid(boundary: LngLatTuple[]): void {
  if (!Array.isArray(boundary) || boundary.length < 3) throw new Error('boundary needs at least three vertices')
  for (const v of boundary) {
    if (!Array.isArray(v) || v.length !== 2 || !Number.isFinite(v[0]) || !Number.isFinite(v[1])) throw new Error('boundary vertex must be [lng, lat]')
    if (v[0] < -180 || v[0] > 180 || v[1] < -90 || v[1] > 90) throw new Error('boundary vertex out of range')
  }
}

export function assertPlacementValid(key: string, p: Placement): void {
  const [cls, ...rest] = key.split(':')
  if (rest.length === 0 || !rest.join(':') || !PLACEABLE_CLASSES.has(cls)) throw new Error(`placement key ${JSON.stringify(key)} must be <Class>:<name>`)
  if (![p.x, p.y, p.heading].every(Number.isFinite)) throw new Error(`placement ${key}: x, y, heading must be finite`)
}

// ── localStorage fallback ───────────────────────────────────────────────────

function readLocal(project: string | null): SitesDocument | null {
  try {
    const raw = localStorage.getItem(localSitesKey(project))
    if (!raw) return null
    const parsed = JSON.parse(raw) as SitesDocument
    return parsed && parsed.version === 1 && Array.isArray(parsed.sites) ? parsed : null
  } catch { return null }
}
function writeLocal(project: string | null, doc: SitesDocument): void {
  try { localStorage.setItem(localSitesKey(project), JSON.stringify(doc)) } catch { /* noop */ }
}
function clearLocal(project: string | null): void {
  try { localStorage.removeItem(localSitesKey(project)) } catch { /* noop */ }
}

// ── the store ───────────────────────────────────────────────────────────────

interface SitesState {
  docs: Record<string, SitesDocument>
  loaded: Record<string, true>
  /** Projects with a write the server has not confirmed. */
  dirty: Record<string, true>
  docFor: (project: string | null) => SitesDocument
  siteById: (project: string | null, siteId: string) => Site | null
  ensureLoaded: (project: string | null) => Promise<void>
  upsertSite: (project: string | null, site: Site) => void
  removeSite: (project: string | null, siteId: string) => void
  setSiteBuses: (project: string | null, siteId: string, buses: string[]) => void
  setBoundary: (project: string | null, siteId: string, boundary: LngLatTuple[]) => void
  setPlacement: (project: string | null, siteId: string, key: string, placement: Placement) => void
  removePlacement: (project: string | null, siteId: string, key: string) => void
  renamePlacement: (project: string | null, cls: string, oldName: string, newName: string) => boolean
  /**
   * Arrange (D8): write every packed object's current position as a
   * placement so it becomes stable, and prune the placements the layout
   * reported as orphans (D14 — the one moment they are removed). Any other
   * existing placement is kept.
   */
  arrangeAll: (project: string | null, siteId: string, objects: Array<{ key: string; origin: [number, number]; heading: number }>, orphans: string[]) => void
  /**
   * Arrange all (S2): replace every placement of the site with the given
   * positions — the layout rebuilt by the rules with no placements at all.
   * The caller confirms with the user first; nothing of the old arrangement
   * survives.
   */
  rearrangeAll: (project: string | null, siteId: string, objects: Array<{ key: string; origin: [number, number]; heading: number }>) => void
  /** Replace a project's document wholesale. */
  replaceDocument: (project: string | null, doc: SitesDocument) => void
  resetForTests: () => void
}

const keyOf = (project: string | null): string => project ?? '__local__'
// One frozen instance: `docFor` is used as a selector, and a fresh object per
// call would re-render every subscriber on every store change.
const EMPTY_DOC: SitesDocument = Object.freeze({ version: 1, sites: Object.freeze([]) as unknown as Site[] }) as SitesDocument
const timers = new Map<string, ReturnType<typeof setTimeout>>()

function canWrite(): boolean {
  const { readOnly, readOnlyReason } = useUIStore.getState()
  return evaluateMutation(readOnly, readOnlyReason).allowed
}

export const useSitesStore = create<SitesState>((set, get) => {
  // Mutate a project's document through `fn` (which returns the new
  // document), refuse when read-only, mark dirty, arm the debounce.
  const write = (project: string | null, fn: (doc: SitesDocument) => SitesDocument): void => {
    if (!canWrite()) return
    const k = keyOf(project)
    const next = fn(get().docFor(project))
    set(s => ({ docs: { ...s.docs, [k]: next }, dirty: { ...s.dirty, [k]: true } }))
    scheduleSave(project)
  }
  const editSite = (project: string | null, siteId: string, fn: (site: Site) => Site): void => {
    write(project, doc => ({ ...doc, sites: doc.sites.map(s => (s.id === siteId ? fn(s) : s)) }))
  }
  return {
    docs: {},
    loaded: {},
    dirty: {},
    docFor: project => get().docs[keyOf(project)] ?? EMPTY_DOC,
    siteById: (project, siteId) => get().docFor(project).sites.find(s => s.id === siteId) ?? null,
    ensureLoaded: async project => {
      const k = keyOf(project)
      if (get().loaded[k]) return
      let doc: SitesDocument
      if (!project) {
        doc = readLocal(null) ?? emptySitesDocument()
      } else {
        try {
          doc = await sitesApi.getSites(project)
        } catch {
          // Unreachable server: the local fallback is the best we have. A
          // server that ANSWERED (even with nothing) always wins — see the
          // deleted-and-recreated project name in the tests.
          doc = readLocal(project) ?? emptySitesDocument()
        }
      }
      set(s => ({
        // A write that landed while the GET was in flight wins over the fetch.
        docs: s.dirty[k] ? s.docs : { ...s.docs, [k]: doc },
        loaded: { ...s.loaded, [k]: true },
      }))
    },
    upsertSite: (project, site) => {
      assertSiteValid(site)
      write(project, doc => {
        const exists = doc.sites.some(s => s.id === site.id)
        return { ...doc, sites: exists ? doc.sites.map(s => (s.id === site.id ? site : s)) : [...doc.sites, site] }
      })
    },
    removeSite: (project, siteId) => write(project, doc => ({ ...doc, sites: doc.sites.filter(s => s.id !== siteId) })),
    setSiteBuses: (project, siteId, buses) => editSite(project, siteId, s => ({ ...s, buses: [...buses] })),
    setBoundary: (project, siteId, boundary) => {
      assertBoundaryValid(boundary)
      editSite(project, siteId, s => ({ ...s, boundary: boundary.map(v => [v[0], v[1]] as LngLatTuple) }))
    },
    setPlacement: (project, siteId, key, placement) => {
      assertPlacementValid(key, placement)
      editSite(project, siteId, s => ({ ...s, placements: { ...s.placements, [key]: { ...placement } } }))
    },
    removePlacement: (project, siteId, key) => editSite(project, siteId, s => {
      const rest = { ...s.placements }
      delete rest[key]
      return { ...s, placements: rest }
    }),
    renamePlacement: (project, cls, oldName, newName) => {
      const oldKey = placementKey(cls, oldName), newKey = placementKey(cls, newName)
      const doc = get().docFor(project)
      if (!doc.sites.some(s => oldKey in (s.placements ?? {}))) return false
      write(project, d => ({
        ...d,
        sites: d.sites.map(s => {
          if (!(oldKey in (s.placements ?? {}))) return s
          const { [oldKey]: moved, ...rest } = s.placements
          return { ...s, placements: { ...rest, [newKey]: moved } }
        }),
      }))
      return true
    },
    arrangeAll: (project, siteId, objects, orphans) => editSite(project, siteId, s => {
      // Every existing placement survives unless the layout named it an
      // orphan; a key absent from `objects` for another reason (its bus has
      // no coordinates today) keeps its placement.
      const placements: Record<string, Placement> = { ...s.placements }
      for (const k of orphans) delete placements[k]
      for (const o of objects) placements[o.key] ??= { x: o.origin[0], y: o.origin[1], heading: o.heading }
      return { ...s, placements }
    }),
    rearrangeAll: (project, siteId, objects) => editSite(project, siteId, s => ({
      ...s,
      placements: Object.fromEntries(objects.map(o => [o.key, { x: o.origin[0], y: o.origin[1], heading: o.heading }])),
    })),
    replaceDocument: (project, doc) => {
      doc.sites.forEach(assertSiteValid)
      write(project, () => doc)
    },
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
  }, SITES_SAVE_DEBOUNCE_MS))
}

async function persist(project: string | null): Promise<'server' | 'local' | 'refused'> {
  if (!canWrite()) return 'refused'
  const doc = useSitesStore.getState().docFor(project)
  if (!project) { writeLocal(null, doc); return 'local' }
  try {
    await sitesApi.putSites(project, doc)
    clearLocal(project)
    useSitesStore.setState(s => {
      const dirty = { ...s.dirty }
      delete dirty[keyOf(project)]
      return { dirty }
    })
    return 'server'
  } catch (e) {
    writeLocal(project, doc)
    console.warn('[sites] PUT failed → wrote to localStorage instead', { project, error: e })
    return 'local'
  }
}

export interface FlushSitesResult { status: 'nothing' | 'server' | 'local' | 'refused'; sites: number }

/**
 * Force the pending write for `project` now. After a Save-As the caller
 * passes `previousProject` (the name the edits were made under): the
 * pending document is re-homed to the new name and PUT there — the twin of
 * `flushPendingLayoutToServer` chasing the `__local__` slot.
 */
export async function flushPendingSitesToServer(
  project: string | null,
  opts: { previousProject?: string | null } = {},
): Promise<FlushSitesResult> {
  const store = useSitesStore.getState()
  const k = keyOf(project)
  let dirtyKey: string | null = store.dirty[k] ? k : null
  let movedFromScratch = false
  if (!dirtyKey && opts.previousProject !== undefined) {
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
      useSitesStore.setState(s => {
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
  if (!dirtyKey) return { status: 'nothing', sites: 0 }
  const t = timers.get(dirtyKey)
  if (t) { clearTimeout(t); timers.delete(dirtyKey) }
  const status = await persist(project)
  if (status === 'server' && movedFromScratch) clearLocal(null)
  return { status, sites: useSitesStore.getState().docFor(project).sites.length }
}

/** pagehide / beforeunload: localStorage first, then a keepalive PUT. */
export function persistSitesOnUnload(project: string | null): void {
  const store = useSitesStore.getState()
  const k = keyOf(project)
  if (!store.dirty[k] || !canWrite()) return
  const doc = store.docFor(project)
  writeLocal(project, doc)
  if (!project) return
  try {
    fetch(`/api/projects/${encodeURIComponent(project)}/sites`, {
      method: 'PUT',
      body: JSON.stringify(doc),
      headers: { 'Content-Type': 'application/json', ...rawFetchHeaders('PUT') },
      keepalive: true,
    }).catch(() => { /* localStorage fallback already done */ })
  } catch { /* fall through */ }
}

/**
 * App-level lifecycle: load the active project's document (the map's
 * polygons need it before the 3D view is ever opened) and persist a pending
 * write on pagehide/beforeunload. Mounted once, in App.
 */
export function useSitesLifecycle(project: string | null): void {
  const ensureLoaded = useSitesStore(s => s.ensureLoaded)
  useEffect(() => { void ensureLoaded(project) }, [project, ensureLoaded])
  useEffect(() => {
    const onHide = () => persistSitesOnUnload(project)
    window.addEventListener('pagehide', onHide)
    window.addEventListener('beforeunload', onHide)
    return () => {
      window.removeEventListener('pagehide', onHide)
      window.removeEventListener('beforeunload', onHide)
    }
  }, [project])
}
