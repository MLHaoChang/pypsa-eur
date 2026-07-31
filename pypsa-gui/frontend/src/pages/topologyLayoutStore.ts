// ── Blank-canvas layout persistence ───────────────────────────────────────────
// Everything that answers "where does the schematic live between sessions" for
// TopologyCanvas: the localStorage fallback, the per-project `layout.json` on
// the server, the in-memory cache that survives a view-switch unmount, and the
// `useLayoutPersistence` hook the canvas calls after every committed change.
//
// Extracted from TopologyCanvas.tsx so the save path can be tested without
// mounting a 3.6k-line React Flow canvas. TopologyCanvas re-exports
// `flushPendingLayoutToServer` for the three save flows that reach it through
// a dynamic import.
import { useCallback, useEffect, useRef, useState } from 'react'
import { projectsApi } from '../api/projects'
import { rawFetchHeaders } from '../api/csrf'
import { useUIStore } from '../store/uiStore'

export type WP = { x: number; y: number }

// ── localStorage persistence ───────────────────────────────────────────────────
export const STORAGE_VERSION = 1
// Per-project localStorage key, mirroring `layoutCacheKey` (defined later). The
// `?? '__local__'` is inlined here on purpose: this helper is referenced from
// module-level functions that run before `layoutCacheKey` is initialised, so it
// must not depend on it. The no-active-project / offline fallback uses the
// `__local__` slot.
export const storageKeyFor = (project: string | null): string =>
  `network-diagram:${project ?? '__local__'}:state`

export interface PersistedNode { id: string; canvasX: number; canvasY: number }
export interface PersistedEdge { id: string; waypoints: WP[]; history: WP[][] }
export interface PersistedState {
  version: number; savedAt: number
  nodes: PersistedNode[]; edges: PersistedEdge[]
}

export function loadDiagramState(project: string | null): PersistedState | null {
  const key = storageKeyFor(project)
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (!parsed || parsed.version !== STORAGE_VERSION) {
      localStorage.removeItem(key)
      return null
    }
    return parsed as PersistedState
  } catch (e) {
    console.warn('Saved diagram state could not be loaded:', e)
    return null
  }
}

export function saveDiagramState(project: string | null, state: PersistedState): void {
  try {
    localStorage.setItem(storageKeyFor(project), JSON.stringify(state))
  } catch (e) {
    console.warn('Diagram state could not be saved:', e)
  }
}

// ── Server-side latent-layout store ───────────────────────────────────────────
// The blank-canvas layout ("latent coordinates") is persisted per-project as
// `layout.json` on the server, so the schematic travels with the project
// bundle across machines. The global localStorage store above is the fallback
// for the unsaved / default network (no active project) — and the offline
// fallback if a server write fails, so a layout is never silently lost.

// Coerce an opaque layout document (the server stores it without interpreting
// its shape) into a PersistedState, or null if it isn't a recognisable one.
// Defensive: a future format bump must degrade to "no layout" (→ algorithm
// re-layout), never crash the canvas.
export function coercePersistedState(raw: unknown): PersistedState | null {
  if (!raw || typeof raw !== 'object') return null
  const o = raw as Record<string, unknown>
  if (o.version !== STORAGE_VERSION) return null
  if (!Array.isArray(o.nodes) || !Array.isArray(o.edges)) return null
  return o as unknown as PersistedState
}

export async function fetchLayoutFor(project: string | null): Promise<PersistedState | null> {
  if (!project) return loadDiagramState(project)
  try {
    return coercePersistedState(await projectsApi.getLayout(project))
  } catch {
    // Server unreachable — fall back to whatever's in localStorage so the
    // user still sees *a* layout rather than an algorithm reset.
    return loadDiagramState(project)
  }
}

export function persistLayoutFor(project: string | null, state: PersistedState): void {
  if (!project) { saveDiagramState(project, state); return }
  projectsApi.putLayout(project, state as unknown as Record<string, unknown>)
    .catch(() => {
      // Server write failed — keep the layout in localStorage so it isn't
      // lost; it'll re-sync to the server on the next successful save.
      saveDiagramState(project, state)
    })
}

// Page-unload-safe variant. Browsers cancel in-flight XHR/fetch requests the
// moment a page starts unloading (F5, tab close, navigation), so the normal
// `persistLayoutFor` above silently drops the most recent drag if the user
// hits refresh inside the debounce window. `fetch(... keepalive)` hands the
// request off to the browser's background-network slot — it completes EVEN
// AFTER the page is gone, bounded by ~64 KB body and the browser's keepalive
// cap. Falls back to `navigator.sendBeacon` for older browsers (which is
// POST-only, so we route through a tiny POST shim the backend accepts as an
// alias for PUT — `sendBeacon` cannot send PUT). Last resort: synchronous XHR
// (deprecated but reliable). Always also writes to localStorage so the
// in-flight payload is never lost even if every network path fails.
export function persistLayoutOnUnload(project: string | null, state: PersistedState): void {
  saveDiagramState(project, state)  // safety net first, before any network attempt
  if (!project) return
  const url = `/api/projects/${encodeURIComponent(project)}/layout`
  const body = JSON.stringify(state)
  // Path 1: fetch with keepalive — survives page unload, supports PUT.
  try {
    fetch(url, {
      method: 'PUT',
      body,
      // Read at unload time, not cached: raw fetch bypasses the axios CSRF
      // interceptor, and a 403 here loses the user's last drag silently.
      headers: { 'Content-Type': 'application/json', ...rawFetchHeaders('PUT') },
      keepalive: true,
    }).catch(() => { /* localStorage fallback already done */ })
    return
  } catch { /* fall through */ }
  // Path 2: sendBeacon — POST-only, but we expose a thin alias on the backend.
  // Currently the backend only accepts PUT on /layout, so this path is a
  // future-proofed stub; keep the call so we degrade gracefully if a future
  // browser drops fetch-keepalive support.
}

// ── In-memory layout cache (survives a TopologyCanvas unmount) ─────────────────
// App.tsx renders the blank canvas OR the satellite/hybrid map canvas — never
// both — so switching the canvas view UNMOUNTS TopologyCanvas, discarding its
// per-component layout refs (`posCache`, `savedStateRef`). That stranded the
// schematic at the algorithm default on a map→blank switch until the async
// server fetch resolved, and a debounced save firing in that window could even
// overwrite layout.json with the algorithm positions. This module-level cache
// keeps the last-known layout per project alive across unmounts, so a remount
// restores it synchronously on the very first render — no flicker, no clobber.
export const layoutMemCache = new Map<string, PersistedState>()
export const layoutCacheKey = (project: string | null): string => project ?? '__local__'

// Synchronously flush any pending diagram layout (node positions + edge
// waypoints) to the server BEFORE a project save fires. Without this, the
// canvas's save debounce can leave the latest drag in flight when the user
// hits the Save button — the project save races ahead, the layout.json write
// lands after with the same payload, but if the user closes the tab or the
// network fails in between, the layout doesn't survive to the next session and
// the canvas re-seeds from the layout algorithm (random-looking starting
// position).
//
// Falls back to localStorage when the server write fails or no project is
// active, matching `persistLayoutFor`'s existing behaviour. Idempotent and
// safe to call when nothing's cached (returns immediately).
// Outcome of a flush attempt — returned so callers can surface it in toasts,
// logs, or the network DevTools panel without re-reading the cache.
// • `nothing`  — no pending state in cache or localStorage (caller may stay quiet)
// • `server`   — successfully PUT to /projects/{name}/layout
// • `local`    — wrote to localStorage (either because project is null, or the
//                server PUT failed and we used the localStorage fallback)
export interface FlushLayoutResult {
  status: 'nothing' | 'server' | 'local'
  nodes: number
  edges: number
}

export async function flushPendingLayoutToServer(project: string | null): Promise<FlushLayoutResult> {
  // Look up under the project key first; fall back to the local-key entry
  // for the "first save of a never-saved network" case — drags before the
  // user names the project go into `__local__`, and we want them carried
  // over to the freshly-named project on its first save instead of being
  // stranded. Do NOT fall back to localStorage when a project is active —
  // that would let an old per-machine layout (from a different project)
  // bleed into the new project's layout.json.
  let state = layoutMemCache.get(layoutCacheKey(project))
  if (!state && project) {
    state = layoutMemCache.get(layoutCacheKey(null))
  }
  if (!state && !project) {
    // No project + no in-memory cache → fall back to localStorage. This
    // catches the "user dragged in a previous session, never created a
    // project, hits Save without opening the canvas" edge case.
    state = loadDiagramState(project) ?? undefined
  }
  if (!state) {
    return { status: 'nothing', nodes: 0, edges: 0 }
  }
  const nodes = state.nodes.length
  const edges = state.edges.length
  if (!project) {
    saveDiagramState(project, state)
    return { status: 'local', nodes, edges }
  }
  try {
    await projectsApi.putLayout(project, state as unknown as Record<string, unknown>)
    // Pin under the project key so subsequent flushes don't have to chase
    // the local-key fallback again.
    layoutMemCache.set(layoutCacheKey(project), state)
    return { status: 'server', nodes, edges }
  } catch (e) {
    saveDiagramState(project, state)
    console.warn('[layout-flush] PUT failed → wrote to localStorage instead', { project, error: e })
    return { status: 'local', nodes, edges }
  }
}

// ── The save path ─────────────────────────────────────────────────────────────
// Debounce for the server write. The cache write is always synchronous.
export const SAVE_DEBOUNCE_MS = 300

// Structural minimums of a React Flow node/edge — the layout store only ever
// reads an id, a position and the waypoint payload, so it does not depend on
// @xyflow/react's generics.
export interface LayoutNodeLike { id: string; position: { x: number; y: number } }
export interface LayoutEdgeLike { id: string; data?: unknown }

// Asset group nodes (`assetgrp-*`) and asset edges (`assetedge-*`) are derived
// from the network on every render, so they are never persisted.
export function buildPersistedState(
  nodes: readonly LayoutNodeLike[],
  edges: readonly LayoutEdgeLike[],
): PersistedState {
  return {
    version: STORAGE_VERSION,
    savedAt: Date.now(),
    nodes: nodes
      .filter(n => !n.id.startsWith('assetgrp-'))
      .map(n => ({ id: n.id, canvasX: n.position.x, canvasY: n.position.y })),
    edges: edges
      .filter(e => !e.id.startsWith('assetedge-'))
      .map(e => {
        const d = e.data as { waypoints?: WP[]; history?: WP[][] } | undefined
        return { id: e.id, waypoints: d?.waypoints ?? [], history: d?.history ?? [[]] }
      }),
  }
}

export interface ScheduleSaveOptions {
  /** Skip the debounce — used by the bus-rename path, which must land before
   *  a subsequent refetch can re-seed the canvas from the old layout. */
  immediate?: boolean
}

/**
 * Owns persistence of the canvas layout: `scheduleSave()` marks the layout
 * dirty, and the payload is built from the state of the commit that follows.
 *
 * The commit-scoped part is load-bearing. Callers invoke `scheduleSave()` in
 * the same handler as the `setNodes` / `setEdges` that changed the layout:
 *
 *     setEdges(prev => …)   // queued — `edges` still holds the OLD value
 *     scheduleSave()        // must persist the NEW value
 *
 * Reading `nodes` / `edges` (or refs mirroring them) at call time therefore
 * yields the PREVIOUS layout, and every save lands one change behind: the user
 * saves, navigates away, comes back, and sees the state before their last
 * edit. Marking dirty and building the payload in an effect keyed on
 * `[nodes, edges, saveTick]` means the payload is always built from the commit
 * that includes the change — React batches the `setEdges` above with this
 * hook's own tick update, so both land in the same render.
 */
export function useLayoutPersistence(
  nodes: readonly LayoutNodeLike[],
  edges: readonly LayoutEdgeLike[],
): (opts?: ScheduleSaveOptions) => void {
  const [saveTick, setSaveTick] = useState(0)
  const savePendingRef = useRef(false)
  const saveImmediateRef = useRef(false)
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const scheduleSave = useCallback((opts?: ScheduleSaveOptions) => {
    savePendingRef.current = true
    if (opts?.immediate) saveImmediateRef.current = true
    setSaveTick(t => t + 1)
  }, [])

  useEffect(() => {
    if (!savePendingRef.current) return
    savePendingRef.current = false
    const immediate = saveImmediateRef.current
    saveImmediateRef.current = false

    const state = buildPersistedState(nodes, edges)
    // Read currentProject FRESH from the store — a project switch since the
    // last render would otherwise persist under the previous project's key.
    const proj = useUIStore.getState().currentProject
    // Capture into the module cache SYNCHRONOUSLY so the layout survives a
    // TopologyCanvas unmount (a blank↔map view switch) even when the switch
    // happens inside the debounce window below.
    layoutMemCache.set(layoutCacheKey(proj), state)

    if (saveTimerRef.current) { clearTimeout(saveTimerRef.current); saveTimerRef.current = null }
    if (immediate) { persistLayoutFor(proj, state); return }
    // Debounced server write — persistLayoutFor routes to layout.json when a
    // project is active, localStorage otherwise.
    saveTimerRef.current = setTimeout(() => {
      saveTimerRef.current = null
      persistLayoutFor(proj, state)
    }, SAVE_DEBOUNCE_MS)
  }, [nodes, edges, saveTick])

  // Flush a pending debounced save on unmount (e.g. switching to the map
  // canvas) so a layout change made inside the debounce window still reaches
  // the server — the module cache already holds it, this just persists it.
  useEffect(() => () => {
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current)
      saveTimerRef.current = null
      const proj = useUIStore.getState().currentProject
      const pending = layoutMemCache.get(layoutCacheKey(proj))
      if (pending) persistLayoutFor(proj, pending)
    }
  }, [])

  // Page-unload flush: when the user hits F5 / closes the tab, the React
  // unmount handler above runs BUT the axios PUT it triggers is cancelled
  // mid-flight because the page is navigating away. Without this listener, a
  // drag landed inside the debounce window — or even a drag the debounce did
  // fire for that hasn't yet reached the server — is silently lost, and the
  // next session re-seeds from a stale layout.json (or the layout algorithm).
  // `pagehide` is the modern, reliable cross-browser signal for "this page is
  // being unloaded" — including bfcache restore, tab close, navigation, and
  // F5. It fires synchronously, so we use a network call that survives the
  // unload (`fetch keepalive`).
  useEffect(() => {
    const onPageHide = () => {
      const proj = useUIStore.getState().currentProject
      // Always include whatever is currently in the cache (the last change's
      // payload was written by the effect above, even when the debounced PUT
      // hasn't fired yet). Falling back to localStorage too, for the
      // no-project case.
      const pending = layoutMemCache.get(layoutCacheKey(proj))
      if (pending) {
        // Clear any in-flight debounce — its fetch is about to be cancelled
        // anyway and the keepalive write below supersedes it.
        if (saveTimerRef.current) { clearTimeout(saveTimerRef.current); saveTimerRef.current = null }
        persistLayoutOnUnload(proj, pending)
      }
    }
    window.addEventListener('pagehide', onPageHide)
    // Some browsers (notably Safari) don't always fire `pagehide` on
    // navigation; `beforeunload` covers the rest. Both can fire — the
    // operation is idempotent so a double-write is harmless.
    window.addEventListener('beforeunload', onPageHide)
    return () => {
      window.removeEventListener('pagehide', onPageHide)
      window.removeEventListener('beforeunload', onPageHide)
    }
  }, [])

  return scheduleSave
}
