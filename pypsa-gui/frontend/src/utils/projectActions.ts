import type { QueryClient } from '@tanstack/react-query'
import { projectsApi } from '../api/projects'
import { networkApi } from '../api/network'
import { simulationApi } from '../api/simulation'
import { appLog, useSimulationStore } from '../store/simulationStore'
import { useUIStore } from '../store/uiStore'

// Query keys invalidated by any operation that swaps the underlying PyPSA
// network in memory (load, restore, import). Includes:
//   - Component tables (buses, lines, …)
//   - Meta + carriers + snapshots
//   - Solver-state queries (`results`, `solverConfig`, `investmentPeriods`)
//     because restore / load can change which solution is in memory
//   - `undoInfo` because the undo stack is cleared on swap
// Update this list when adding any feature that caches network-derived data —
// otherwise a restore will silently keep serving pre-restore values.
export const ALL_NETWORK_KEYS = [
  'buses', 'lines', 'links', 'generators', 'loads', 'storage_units',
  'stores', 'transformers', 'meta', 'load_profiles', 'generator_profiles',
  'link_profiles', 'timeseries', 'carriers', 'snapshots', 'undoInfo',
  'results', 'solverConfig', 'investmentPeriods',
  // Status query shared by App/AppHeader/SnapshotPicker/Results/OverviewPanel/
  // SolverSettings — invalidate on network swap so the StatusBar / picker
  // don't lag behind a project load by up to their staleTime.
  'simulationStatus',
] as const

export const DIAGRAM_STATE_KEY = 'network-diagram:default:state'

export function invalidateNetworkQueries(qc: QueryClient): void {
  ALL_NETWORK_KEYS.forEach(k => qc.invalidateQueries({ queryKey: [k] }))
  qc.invalidateQueries({ queryKey: ['projects'] })
  // Any network-swapping op (load / restore / import) changes a project's
  // on-disk state, so an open Compare panel's cached `['compare-state', *]`
  // is now stale. Invalidate the whole family — every switch flow
  // (ProjectTabs, Sidebar, CommandPalette, snapshot restore) routes through
  // this helper, so one line here closes the gap for all of them.
  qc.invalidateQueries({ queryKey: ['compare-state'] })
}

/**
 * Abort any in-flight simulation and wait for the PyPSA lock to ACTUALLY
 * release before returning. Project-switch flows call this before any
 * save / load that needs the lock — otherwise the load blocks at axios'
 * 30 s timeout.
 *
 * Why `getLockStatus` instead of `getStatus.running`:
 *
 *   The `running` flag flips to `false` THE MOMENT `/abort` is called
 *   (the abort endpoint just sets the `stop_event`). The PyPSA lock is
 *   STILL HELD by the worker thread, because HiGHS / Gurobi native code
 *   doesn't check `stop_event` until it finishes the current LP iteration
 *   — which can take seconds to tens of seconds on a real-sized LP.
 *   Polling `running` returns `false` after 1 probe (0.0 s); the load
 *   then blocks on the lock for 30 s and times out.
 *
 *   `getLockStatus` is authoritative: it does
 *   `lock.acquire(blocking=False)` server-side and reports the actual
 *   lock state. Returns instantly, can be polled every 500 ms.
 *
 * Total budget is 60 s — a real HiGHS iteration on a year-of-hourly-data
 * SCLOPF problem can take 10–30 s, so the solver might need ~1 iteration
 * worth of time to acknowledge the abort. We poll every 500 ms; once
 * `lock_held === false && worker_alive === false`, return true.
 *
 * Returns false on:
 *   * Total budget exceeded — surface "wait longer or restart backend"
 *   * Per-probe timeout — backend is truly hung (rare)
 */
export async function abortRunningSim(): Promise<boolean> {
  const SHORT_TIMEOUT_MS = 2_500
  const TOTAL_BUDGET_MS = 60_000
  const POLL_INTERVAL_MS = 500

  // Authoritative pre-check: is the lock free RIGHT NOW? If yes, nothing
  // to abort. (Store-flag check would be wrong: status could still say
  // 'running' from a previous session before the SSE stream updated.)
  let preState: { lock_held: boolean; worker_alive: boolean } | null = null
  try {
    preState = await simulationApi.getLockStatus(SHORT_TIMEOUT_MS)
  } catch {
    appLog('WARN', 'Backend lock_status probe failed — proceeding optimistically')
    return true
  }
  if (!preState.lock_held && !preState.worker_alive) return true

  appLog('INFO', 'Aborting in-flight simulation before switching projects…')
  try {
    await simulationApi.abortFast(SHORT_TIMEOUT_MS)
  } catch (e) {
    // 400 "No simulation running" → races with status-flip; the lock
    // poll below will confirm. Timeout → backend stuck.
    const msg = String((e as Error)?.message ?? e)
    if (/timeout/i.test(msg)) {
      appLog('ERROR',
        'Abort request timed out — backend appears to be hung in solver '
        + 'native code. Restart the backend to clear the stuck PyPSA lock.')
      return false
    }
    appLog('WARN', `abort returned: ${msg}`)
  }

  const started = Date.now()
  let probes = 0
  let lastNotice = started
  while (Date.now() - started < TOTAL_BUDGET_MS) {
    probes += 1
    try {
      const s = await simulationApi.getLockStatus(SHORT_TIMEOUT_MS)
      if (!s.lock_held && !s.worker_alive) {
        appLog('INFO',
          `Solver released the lock after ${((Date.now() - started) / 1000).toFixed(1)}s `
          + `(${probes} probe(s)).`)
        return true
      }
      // Periodic user-facing reminder during long waits — HiGHS can hold
      // the lock for up to one full LP iteration after abort is sent.
      // Without this the UI looks frozen for 30 s+ with no feedback.
      const elapsed = Date.now() - started
      if (elapsed - (lastNotice - started) >= 5000) {
        appLog('INFO',
          `Still waiting for solver to release the lock (${(elapsed / 1000).toFixed(0)}s)…`
          + ' HiGHS only checks the abort flag between LP iterations.')
        lastNotice = Date.now()
      }
    } catch (e) {
      const msg = String((e as Error)?.message ?? e)
      if (/timeout/i.test(msg)) {
        appLog('ERROR',
          `lock_status probe timed out at ${((Date.now() - started) / 1000).toFixed(1)}s `
          + '— backend may be hung. Restart manually if this persists.')
        return false
      }
    }
    await new Promise(r => setTimeout(r, POLL_INTERVAL_MS))
  }
  appLog('ERROR',
    `Solver did not release the PyPSA lock within ${TOTAL_BUDGET_MS / 1000}s of abort. `
    + 'It may still be in a long iteration — wait a moment and retry, '
    + 'or restart the backend.')
  return false
}

// Best-effort save of the current project. Returns true on success, false on
// any failure (including 409 empty-network refusal). Never throws so callers
// can chain it before destructive switches without wrapping in try/catch.
// `clearUndo` defaults to false here because background/automatic saves should
// not erase the user's revert history — only explicit Save clicks do that.
export async function saveProjectQuietly(name: string, clearUndo = false): Promise<boolean> {
  // Mid-solve the worker thread holds the PyPSA lock; save_project would
  // block on metadata-write lock acquisition until either the solve
  // finishes or axios times out at 30s. Project-switch flows call
  // abortRunningSim() FIRST, so reaching this branch means either (a) the
  // abort succeeded and the worker released the lock (good), or (b) a
  // caller forgot to call abortRunningSim. Either way, attempting the
  // save while status is still running is guaranteed to fail noisily — so
  // skip with a clear INFO line and let the caller decide what to do next.
  if (useSimulationStore.getState().status === 'running') {
    appLog('INFO', `Skipped save of '${name}' — simulation in progress`)
    return false
  }
  try {
    // Assert identity when saving what we believe is the ACTIVE project: the
    // backend refuses (409) if its in-memory network is actually bound to a
    // different project. Both callers of this helper save `currentProject`, so
    // name === currentProject here — but derive it defensively rather than
    // assume. Omit `expect` when saving under a different name (no caller does
    // today, but keeps the helper honest).
    const expect = name === useUIStore.getState().currentProject ? name : undefined
    const res = await projectsApi.save(name, false, clearUndo, expect)
    appLog('INFO', `Auto-saved '${name}' (${res.ts_columns_saved} ts cols)`)
    // Flush the diagram layout (node positions + edge waypoints) to
    // layout.json AFTER the network save — the project directory must
    // exist on disk before PUT /projects/{name}/layout will accept the
    // write. Without this flush, a debounced drag (300 ms window) can
    // be lost when the user immediately closes the tab; with it, the
    // next load reseeds the saved positions instead of running the
    // layout algorithm from scratch.
    // Lazy import keeps the cyclic dep risk at zero.
    try {
      const { flushPendingLayoutToServer } = await import('../pages/TopologyCanvas')
      const r = await flushPendingLayoutToServer(name)
      if (r.status === 'server') {
        appLog('INFO', `Layout saved · ${r.nodes} node(s) + ${r.edges} edge(s) → layout.json`)
      } else if (r.status === 'local') {
        appLog('WARN', `Layout server write failed — kept ${r.nodes} node(s) + ${r.edges} edge(s) in localStorage`)
      }
    } catch (e) {
      appLog('WARN', `Layout flush failed for '${name}': ${String((e as Error)?.message ?? e)}`)
    }
    // Stamp the saved-time so the Recents row / StatusBar reflect the
    // background save even when the caller (project-switch flow, tab-switch)
    // never explicitly calls markProjectSaved. Zustand's getState() lets us
    // dispatch from a non-React caller.
    useUIStore.getState().markProjectSaved(name)
    return true
  } catch (e) {
    appLog('WARN', `Could not save '${name}': ${String((e as Error)?.message ?? e)}`)
    return false
  }
}

// Format a past ISO timestamp as a short "saved Xs / Xm / Xh / Xd ago" string.
// Returns null for absent / unparseable / future-dated input so callers can
// branch cleanly on "have we ever saved this project?".
export function formatRelativeTime(iso: string | null | undefined, nowMs?: number): string | null {
  if (!iso) return null
  const then = Date.parse(iso)
  if (Number.isNaN(then)) return null
  const now = nowMs ?? Date.now()
  const diff = Math.max(0, now - then)
  if (diff < 5_000) return 'just now'
  const sec = Math.floor(diff / 1000)
  if (sec < 60) return `${sec}s ago`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min}m ago`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr}h ago`
  const day = Math.floor(hr / 24)
  if (day < 7) return `${day}d ago`
  // Beyond a week, surface the calendar date so the user doesn't get
  // misleading "23d ago" labels they can't act on. Avoids `toLocaleDateString`
  // because Chrome respects Windows regional setting (same locale-quirk as
  // the datetime-local placeholder issue documented in CLAUDE.md).
  const d = new Date(then)
  const yy = d.getFullYear()
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${yy}-${mm}-${dd}`
}

// Pick a fresh "Untitled N" name not already in the open-tabs list.
export function nextUntitledName(taken: readonly string[]): string {
  const set = new Set(taken)
  if (!set.has('Untitled')) return 'Untitled'
  for (let i = 2; i < 10_000; i++) {
    const candidate = `Untitled ${i}`
    if (!set.has(candidate)) return candidate
  }
  return `Untitled ${Date.now()}`
}

// Slugify a free-form project title into a backend-friendly name.
export function slugify(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/, '') || 'network'
}

// Resolve a collision-free on-disk project id for a NEW-project flow.
//
// The quick "+" create path seeds an EMPTY network under `name` with
// force=true, which BYPASSES the backend's empty-network-overwrite guard
// (projects.py save_project). Without this check, creating a project whose
// slug matches an existing-but-unopened project silently wipes that project's
// network.nc to a 0-bus shell. We compare against the full on-disk project
// list — not just open tabs — and suffix (-2, -3, …) on collision, the same
// way the backend uniquifies template names. `name` must already be slugified
// by the caller; the backend stores project ids verbatim (the ProjectTabs "+"
// path always passes a slug, so its ids ARE slugs), so the comparison is
// apples-to-apples and a slug can only collide with another slug-named project.
//
// THROWS if the project list can't be read: the caller cannot prove the name
// is free, so it MUST abort the destructive seed rather than risk an overwrite.
export async function uniqueProjectName(name: string): Promise<string> {
  const existing = await projectsApi.list()
  const taken = new Set(existing.map(p => p.name))
  if (!taken.has(name)) return name
  for (let i = 2; i < 10_000; i++) {
    const candidate = `${name}-${i}`
    if (!taken.has(candidate)) return candidate
  }
  return `${name}-${Date.now()}`
}

// Per-project cache of FileSystemFileHandle returned by showSaveFilePicker.
// First Save for a project prompts for a location and stores the handle here;
// subsequent Saves reuse the handle so the user isn't asked again. The cache
// is in-memory only — a page reload drops it (acceptable trade-off vs the
// IndexedDB persistence cost). "Save a Copy" deliberately bypasses this map.
//
// FileSystemFileHandle is typed as `unknown` here because the TypeScript lib
// version varies and we already gate the API behind a runtime check.
const _bundleHandles = new Map<string, unknown>()

export type BundleSaveResult = 'picker' | 'reused' | 'download' | 'cancelled'

interface BundleSaveOptions {
  // Always show the OS save-file picker, even if a handle is cached for this
  // project name. Used by "Save a Copy" so each copy lands where the user
  // tells it to.
  askLocation?: boolean
  // Don't update the cache after a successful pick. Used by "Save a Copy"
  // so subsequent regular Saves of the original project don't get redirected
  // to the copy's destination.
  skipCache?: boolean
}

// Fetch the freshly-saved bundle for `name` and write it to disk.
//   - First Save: prompts for a location via showSaveFilePicker, caches the handle.
//   - Subsequent Saves: reuses the cached handle, no prompt.
//   - askLocation=true (Save a Copy): always prompt; skipCache=true to keep
//     the cache pointing at the original project's location.
//   - Browsers without showSaveFilePicker (Firefox/Safari): fall back to a
//     download-anchor save into the browser's default download folder.
//
// Returns:
//   'picker'    — picker shown, file written
//   'reused'    — cached handle reused (no prompt)
//   'download'  — fallback path for browsers without the File System Access API
//   'cancelled' — user dismissed the picker dialog
export async function downloadProjectBundle(
  name: string,
  options?: BundleSaveOptions,
): Promise<BundleSaveResult> {
  const blob = await projectsApi.downloadBundle(name)
  const askLocation = options?.askLocation ?? false
  const skipCache = options?.skipCache ?? false

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const w = window as any
  if (typeof w.showSaveFilePicker !== 'function') {
    // Browser doesn't expose the File System Access API — there's no concept
    // of a persistent file handle, so the per-project cache is moot. Just
    // download to the browser's default folder.
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${name}.pypsaproj.zip`
    a.click()
    URL.revokeObjectURL(url)
    return 'download'
  }

  // Try the cached handle first when caller hasn't opted out.
  if (!askLocation) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const cached = _bundleHandles.get(name) as any
    if (cached) {
      try {
        // queryPermission / requestPermission are part of the File System
        // Access API. They're not always present (older Chromium); guard
        // against missing methods.
        const perm: string | undefined = await cached.queryPermission?.({ mode: 'readwrite' })
        let granted = perm === 'granted'
        if (perm === 'prompt' && typeof cached.requestPermission === 'function') {
          const newPerm: string = await cached.requestPermission({ mode: 'readwrite' })
          granted = newPerm === 'granted'
        }
        if (granted) {
          const writable = await cached.createWritable()
          await writable.write(blob)
          await writable.close()
          return 'reused'
        }
        // Permission denied — fall through to the picker so the user can
        // choose a new location.
      } catch {
        // Stale handle (file moved/deleted, permissions revoked, etc.).
        // Drop it so the next pick rebuilds the cache cleanly.
        _bundleHandles.delete(name)
      }
    }
  }

  // Show the picker (first save for this project, or askLocation requested).
  try {
    const handle = await w.showSaveFilePicker({
      suggestedName: `${name}.pypsaproj.zip`,
      types: [{ description: 'PyPSA project bundle', accept: { 'application/zip': ['.pypsaproj.zip'] } }],
    })
    const writable = await handle.createWritable()
    await writable.write(blob)
    await writable.close()
    if (!skipCache) {
      _bundleHandles.set(name, handle)
    }
    return 'picker'
  } catch (e) {
    if ((e as { name?: string })?.name === 'AbortError') return 'cancelled'
    throw e
  }
}

// Forget the cached file handle for a project — call when a project is
// deleted or renamed so the next save doesn't write to a stale location.
export function forgetBundleLocation(name: string): void {
  _bundleHandles.delete(name)
}

// Trigger a browser file save for the given blob.
// Uses File System Access API (showSaveFilePicker) where available so the user
// gets a real "Save As" dialog with folder choice; falls back to a download
// anchor (browser-default download folder) elsewhere.
export async function saveBlobToDisk(
  blob: Blob,
  suggestedName: string,
  description: string,
  acceptExt: string,
): Promise<'picker' | 'download'> {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const w = window as any
  if (typeof w.showSaveFilePicker === 'function') {
    try {
      const handle = await w.showSaveFilePicker({
        suggestedName,
        types: [{ description, accept: { 'application/zip': [acceptExt] } }],
      })
      const writable = await handle.createWritable()
      await writable.write(blob)
      await writable.close()
      return 'picker'
    } catch (e) {
      // AbortError → user cancelled; rethrow so caller can no-op
      if ((e as { name?: string })?.name === 'AbortError') throw e
      // Otherwise fall through to download fallback
    }
  }
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = suggestedName
  a.click()
  URL.revokeObjectURL(url)
  return 'download'
}

// Reset the in-memory backend network and clear the cached diagram layout.
export async function resetBackendNetwork(): Promise<void> {
  await networkApi.resetNetwork()
  try { localStorage.removeItem(DIAGRAM_STATE_KEY) } catch { /* noop */ }
}
