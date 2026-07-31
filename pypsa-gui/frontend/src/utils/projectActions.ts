import type { QueryClient } from '@tanstack/react-query'
import { projectsApi } from '../api/projects'
import { networkApi } from '../api/network'
import { simulationApi } from '../api/simulation'
import { appLog, useSimulationStore } from '../store/simulationStore'
import { useUIStore } from '../store/uiStore'
import { authEnabled } from '../auth/config'
import { lockStateFromAcquire, WRITABLE, type LockInfo, type LockAcquireOutcome } from './lockState'
import { nk } from './queryKeys'
import { flushPendingEdgeDeletes } from './pendingEdgeDeletes'

// Query keys invalidated by any operation that swaps the underlying PyPSA
// network in memory (load, restore, import). Includes:
//   - Component tables (buses, lines, …)
//   - Meta + carriers + snapshots
//   - Solver-state queries (`results`, `solverConfig`, `investmentPeriods`)
//     because restore / load can change which solution is in memory
// Each of these roots is PROJECT-NAMESPACED via `nk` (see queryKeys.ts), so
// invalidateNetworkQueries scopes them to one project's namespace.
// Update this list when adding any feature that caches network-derived data —
// otherwise a restore will silently keep serving pre-restore values.
export const ALL_NETWORK_KEYS = [
  'buses', 'lines', 'links', 'generators', 'loads', 'storage_units',
  'stores', 'transformers', 'meta', 'load_profiles', 'generator_profiles',
  'link_profiles', 'timeseries', 'timeseries-multi', 'load_aggregate',
  'carriers', 'snapshots',
  'results', 'solverConfig', 'investmentPeriods',
  // Vintage per-period bounds + the solved vintage capacities, and the
  // standalone result roots used by ResultsViewer / SolveQueuePanel — all
  // network-derived, so a load/restore/import must drop them too. (The
  // legacy prefix-only `['results']` invalidation never reached these
  // sibling roots; now that every root is project-namespaced via `nk`, list
  // them explicitly so a network swap clears the lot.)
  'vintageBounds', 'vintage_results', 'preflight',
  'results_generators', 'results_lines', 'results_loads', 'results_prices',
  'results_storage', 'results_statistics', 'resultsBundle',
  // Status query shared by App/AppHeader/SnapshotPicker/Results/OverviewPanel/
  // SolverSettings — invalidate on network swap so the StatusBar / picker
  // don't lag behind a project load by up to their staleTime.
  'simulationStatus',
  // Undo depth (unsaved-edit badge). B7 inc2 rekeyed every `undoInfo` query to
  // `nk(currentProject, 'undoInfo')`, so it's now per-project and invalidated
  // here alongside the rest — a load/restore/import must refetch the new
  // project's depth (it carries its own undo stack server-side, B3).
  'undoInfo',
] as const

// Matches every per-project diagram-state localStorage key written by
// TopologyCanvas (`network-diagram:<project|__local__>:state`). Kept as an
// exported const for stability; `resetBackendNetwork` sweeps ALL matching keys.
export const DIAGRAM_STATE_KEY_RE = /^network-diagram:.*:state$/

/**
 * Drop ALL of one project's retained React Query cache entries (B9).
 *
 * When the backend evicts a project from its resident registry (LRU, over
 * RESIDENT_CAP), its in-memory network is gone server-side; the client should
 * release the matching RETAINED cache so its RAM mirrors the server. Unlike
 * `invalidateNetworkQueries` (which marks entries stale → refetch), this
 * `removeQueries` PURGES them — the project re-hydrates cold (fetch on mount +
 * a backend re-resolve via the path-scoped dependency) if the user revisits it.
 *
 * Every per-project root is namespaced via `nk(projectId, root)`, so removing
 * `[root, projectId]` scopes the purge to exactly this project — other resident
 * projects' caches are untouched. We sweep `ALL_NETWORK_KEYS` plus the
 * project-keyed compare-state family. The open tab is deliberately LEFT in
 * place: eviction is a memory concern, not a UX one — the tab stays and the
 * project re-hydrates if clicked.
 */
export function dropProjectCache(qc: QueryClient, projectId: string): void {
  ALL_NETWORK_KEYS.forEach(k => qc.removeQueries({ queryKey: nk(projectId, k) }))
  qc.removeQueries({ queryKey: ['compare-state', projectId] })
}

export function invalidateNetworkQueries(qc: QueryClient, projectId: string | null): void {
  // Project-scoped: each cache entry is namespaced by project (see `nk`), so
  // invalidate ONLY the named project's network roots. A prefix-only
  // `invalidateQueries({ queryKey: [k] })` would also nuke every OTHER
  // resident project's cache — fine while single-active, but it would defeat
  // the multi-project cache-retention benefit B7/B8 exists to enable.
  ALL_NETWORK_KEYS.forEach(k => qc.invalidateQueries({ queryKey: nk(projectId, k) }))
  // `projects` is a GLOBAL list (not per-project) — keep it flat.
  qc.invalidateQueries({ queryKey: ['projects'] })
  // Any network-swapping op (load / restore / import) changes a project's
  // on-disk state, so an open Compare panel's cached `['compare-state', *]`
  // is now stale. `compare-state` is keyed `['compare-state', <project>]`, so
  // invalidate this project's entry; the whole family invalidation isn't
  // needed because each project's compare-state is independent. Every switch
  // flow (ProjectTabs, Sidebar, CommandPalette, snapshot restore) routes
  // through this helper, so one line here closes the gap for all of them.
  qc.invalidateQueries({ queryKey: ['compare-state', projectId] })
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

// Result of the shared instant-switch flow. Callers map this to their own
// toast / appLog conventions (which differ across the four entry points).
//   'switched'  — `target` is now the active project (instant if its cache was
//                 warm; cold projects fetch on mount, which is expected).
//   'noop'      — `target` was already the current project; nothing happened.
//   'abort-failed' — couldn't stop an in-flight FOREGROUND solve; the active
//                 project is unchanged.
//   'busy-solve' — a FOREGROUND solve is in flight (backend returned 409);
//                 the user must finish/abort it before switching.
//   'not-found' — `target` no longer exists on disk (backend 404).
//   'error'     — any other failure (network, unexpected status); message in
//                 `.message`.
export type SwitchResult =
  | { status: 'switched' }
  | { status: 'noop' }
  | { status: 'abort-failed' }
  | { status: 'busy-solve' }
  | { status: 'not-found' }
  | { status: 'error'; message: string }

// ── Multi-user edit lock (Task 14) ──────────────────────────────────────────
//
// In auth mode a project can be edited by ONE user at a time. On switch we
// POST /lock to claim it; success → we hold the lock and start a heartbeat that
// refreshes the server-side TTL; a 409 → someone else holds it, so we open the
// project READ-ONLY (still activated for viewing) and surface the holder in the
// banner. The heartbeat is a module-level singleton bound to one project id so
// a rapid switch A→B can't leave two intervals racing.
//
// Backend TTL is 120 s; refresh well inside that so a slow tick / transient
// blip doesn't drop the lock. In-memory only — a reload re-acquires via the
// App mount effect + the switch flow.
const LOCK_HEARTBEAT_MS = 45_000

let _heartbeatTimer: ReturnType<typeof setInterval> | null = null
let _heartbeatProject: string | null = null

function _lockFromErrorDetail(e: unknown): LockInfo | null {
  const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (detail && typeof detail === 'object' && 'lock' in detail) {
    return ((detail as { lock?: LockInfo | null }).lock) ?? null
  }
  return null
}

function _applyLock(outcome: LockAcquireOutcome): void {
  useUIStore.getState().setLockState(lockStateFromAcquire(outcome))
}

// Stop the heartbeat interval (idempotent). Called on release, on losing the
// lock, and before starting a heartbeat for a different project.
export function stopLockHeartbeat(): void {
  if (_heartbeatTimer) {
    clearInterval(_heartbeatTimer)
    _heartbeatTimer = null
  }
  _heartbeatProject = null
}

function startLockHeartbeat(projectId: string): void {
  stopLockHeartbeat()
  _heartbeatProject = projectId
  _heartbeatTimer = setInterval(() => {
    // Defend against a stale timer that outlived a project switch.
    if (_heartbeatProject !== projectId) return
    projectsApi.heartbeatLock(projectId)
      .then(res => _applyLock({ ok: true, lock: res.lock }))
      .catch((e) => {
        const status = (e as { response?: { status?: number } })?.response?.status
        if (status === 409) {
          // Lost the lock — it expired and another user grabbed it, or the
          // server dropped it. Fall to read-only and stop pinging.
          _applyLock({ ok: false, lock: _lockFromErrorDetail(e) })
          stopLockHeartbeat()
          appLog('WARN', `Lost the edit lock on '${projectId}' — the workbench is now read-only.`)
        }
        // Other errors (network blip during a backend reload) are transient;
        // keep the lock optimistically and retry on the next tick.
      })
  }, LOCK_HEARTBEAT_MS)
}

// Acquire the edit lock for a project (auth mode only). On success the UI is
// writable and a heartbeat starts; on a 409 (or any failure) the project stays
// ACTIVATED for viewing but the workbench drops into read-only mode. Returns
// the resulting `readOnly` flag. A no-op returning false when auth is disabled
// — the legacy single-user workbench is always writable.
export async function acquireProjectLock(projectId: string): Promise<boolean> {
  if (!authEnabled) {
    useUIStore.getState().setLockState(WRITABLE)
    return false
  }
  try {
    const res = await projectsApi.acquireLock(projectId)
    _applyLock({ ok: true, lock: res.lock })
    startLockHeartbeat(projectId)
    return false
  } catch (e) {
    stopLockHeartbeat()
    const lock = _lockFromErrorDetail(e)
    // Both a 409 and an unexpected failure resolve to read-only: never let two
    // users each believe they hold the edit lock.
    _applyLock({ ok: false, lock })
    const status = (e as { response?: { status?: number } })?.response?.status
    if (status === 409) {
      appLog('WARN', `'${projectId}' is being edited${lock?.holder_email ? ` by ${lock.holder_email}` : ' by another user'} — opened read-only.`)
    } else {
      appLog('WARN', `Could not acquire the edit lock on '${projectId}' — opened read-only.`)
    }
    return true
  }
}

// Best-effort release of a project's lock (auth mode only) so a teammate can
// pick it up immediately instead of waiting out the TTL. Never throws.
export async function releaseProjectLock(projectId: string): Promise<void> {
  if (!authEnabled) return
  if (_heartbeatProject === projectId) stopLockHeartbeat()
  try {
    await projectsApi.releaseLock(projectId)
  } catch { /* best effort — the TTL reclaims it anyway */ }
}

/**
 * B8 — INSTANT in-memory project switch (the C2 payoff).
 *
 * Replaces the old destructive abort→save→load→invalidateNetworkQueries
 * round-trip. The backend `POST /api/projects/{target}/activate` makes `target`
 * the active context with a pure pointer swap when it's already resident; the
 * frontend then re-keys every reactive `useQuery` to `nk(target,…)` (via
 * `setCurrentProject`), so React Query serves `target`'s RETAINED cache with NO
 * refetch. First-ever visit to a project still fetches on mount (cold cache) —
 * that's expected and fine.
 *
 * CRITICAL — this must NOT call `invalidateNetworkQueries`: that would drop
 * `target`'s retained component tables and force a full refetch, defeating the
 * instant switch. Only the small meta/status/snapshots roots are invalidated so
 * the StatusBar / window title / snapshot picker reflect the activated backend.
 *
 * Durability: the OUTGOING project is still saved to disk (its in-memory ctx is
 * retained either way, but the save protects against a crash) before activate.
 *
 * The shared helper does NOT manage the caller's busy-flag or
 * `projectSwitchInProgress` fence — each of the four entry points keeps owning
 * those (set before / clear in `finally`) plus their own toasts. This helper is
 * the network + store core they all share.
 */
export async function switchToProject(target: string, qc: QueryClient): Promise<SwitchResult> {
  const ui = useUIStore.getState()
  const currentProject = ui.currentProject
  if (target === currentProject) return { status: 'noop' }

  // Is the CURRENT project mid-solve via the QUEUE dispatcher? If so, switching
  // away is SAFE and must NOT abort it: the dispatcher solves the active project
  // in place on a CAPTURED ctx (its own network/lock/state sink), so moving the
  // active pointer leaves it solving in the background and saving itself on
  // completion (B4.3). Read the queue list from the React Query cache (the
  // useSolveQueue hook keeps ['solveQueue'] fresh while a job is active).
  const queue = qc.getQueryData<{ jobs: Array<{ project_id: string; status: string }> }>(['solveQueue'])
  const currentSolvingInQueue = !!currentProject && !!queue?.jobs?.some(
    j => j.project_id === currentProject && j.status === 'running',
  )

  if (!currentSolvingInQueue) {
    // 1. Abort any LEGACY foreground /run solve on the current project (a queue
    //    solve is handled above — left running in the background).
    const stopped = await abortRunningSim()
    if (!stopped) return { status: 'abort-failed' }

    // 2. Durability: persist the outgoing project's edits. The in-memory ctx is
    //    retained regardless, but the save guards against a process crash.
    //    SKIPPED when a queue solve is in flight — the network carries transient
    //    LP transforms mid-solve and the backend would 409 the save anyway; the
    //    dispatcher saves the project itself when the solve completes.
    if (currentProject) await saveProjectQuietly(currentProject)
  }

  // 3. Activate the target backend-side (instant pointer swap when resident).
  //    A cold activate may EVICT the LRU resident project(s) to stay under the
  //    server's RESIDENT_CAP (B9); the response lists those ids.
  let activated = target
  let evicted: string[] = []
  try {
    const res = await projectsApi.activate(target)
    activated = res.activated
    evicted = res.evicted ?? []
  } catch (e) {
    const status = (e as { response?: { status?: number } })?.response?.status
    if (status === 409) return { status: 'busy-solve' }
    if (status === 404) return { status: 'not-found' }
    return { status: 'error', message: String((e as Error)?.message ?? e) }
  }

  // 3b. Drop the evicted projects' retained caches so client RAM mirrors the
  //     server-side eviction. The TARGET is never in `evicted` (it's the newly
  //     active id, protected from its own registration), so this can't purge
  //     the cache we're about to serve. Tabs stay open — eviction is RAM, not UX.
  evicted.forEach(id => dropProjectCache(qc, id))

  // 4. Re-key all reactive queries to nk(target,…). React Query serves the
  //    target's retained cache — instant, no refetch when warm.
  useUIStore.getState().setCurrentProject(activated, target !== activated ? target : undefined)
  useUIStore.getState().setProjectName(activated)

  // 5. Refresh ONLY the small lifecycle roots so the StatusBar / title /
  //    snapshot picker reflect the activated backend state. DELIBERATELY not
  //    invalidateNetworkQueries — that would nuke the retained component cache.
  qc.invalidateQueries({ queryKey: nk(activated, 'meta') })
  qc.invalidateQueries({ queryKey: nk(activated, 'simulationStatus') })
  qc.invalidateQueries({ queryKey: nk(activated, 'snapshots') })

  // 6. LRU signal for B9 eviction.
  useUIStore.getState().touchTab(activated)

  // 7. Multi-user edit lock (auth mode). Release the OUTGOING project's lock so
  //    a teammate can grab it now, then claim the target's. If another user
  //    holds the target, acquireProjectLock flips the UI to read-only — the
  //    switch itself still succeeds (the project is activated for viewing).
  //    A no-op when auth is disabled (legacy workbench stays writable).
  if (authEnabled) {
    if (currentProject && currentProject !== activated) {
      void releaseProjectLock(currentProject)
    }
    await acquireProjectLock(activated)
  } else {
    useUIStore.getState().setLockState(WRITABLE)
  }

  return { status: 'switched' }
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
    // Commit any line/link delete still inside its 5 s undo window first —
    // otherwise the backend network still holds an edge the canvas already
    // removed, and the file we write disagrees with what the user sees.
    const drainedDeletes = await flushPendingEdgeDeletes()
    if (drainedDeletes.flushed || drainedDeletes.failed) {
      appLog(drainedDeletes.failed ? 'WARN' : 'INFO',
        `Committed ${drainedDeletes.flushed} pending edge delete(s) before saving '${name}'` +
        (drainedDeletes.failed ? ` · ${drainedDeletes.failed} failed` : ''))
    }
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
//
// The trim regex needs BOTH the `g` flag and `+`. Without them (`/^_|_$/`) the
// alternation replaces only the FIRST match, so a title with junk at both ends
// kept its trailing underscore: "  spaced  " → "spaced_", "!My Project!" →
// "my_project_". Only affects names generated from here on; existing project
// directories keep whatever name they were created with.
export function slugify(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'network'
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
  // Suppress the axios interceptor's own error toast, for callers that show a
  // better one. Because `downloadBundle` sets `responseType: 'blob'`, the
  // interceptor's `data?.detail` lookup reads a Blob and can never find the
  // message — so its toast is the opaque "Request failed with status code 404"
  // even when the server sent a perfectly good reason. A caller that opts out
  // here should use `bundleErrorMessage` to recover that reason.
  skipErrorToast?: boolean
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
  const blob = await projectsApi.downloadBundle(
    name, { skipErrorToast: options?.skipErrorToast },
  )
  const askLocation = options?.askLocation ?? false
  const skipCache = options?.skipCache ?? false

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const w = window as any
  if (typeof w.showSaveFilePicker !== 'function') {
    // Browser doesn't expose the File System Access API — there's no concept
    // of a persistent file handle, so the per-project cache is moot. Just
    // download to the browser's default folder. This is also the path the
    // pywebview desktop shell always takes; WKWebView has no picker.
    return anchorDownload(blob, name)
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
  //
  // The picker and the WRITE get separate try blocks, and the difference is
  // load-bearing. One block covering both meant a write failure — a full disk,
  // a revoked permission — was caught by the picker's fallback: the user was
  // told "Exported", while what existed was a 0-byte file at the location they
  // chose plus a second doomed write to the same full disk.
  let handle
  try {
    handle = await w.showSaveFilePicker({
      suggestedName: `${name}.pypsaproj.zip`,
      types: [{ description: 'PyPSA project bundle', accept: { 'application/zip': ['.pypsaproj.zip'] } }],
    })
  } catch (e) {
    if ((e as { name?: string })?.name === 'AbortError') return 'cancelled'
    // The picker could not be SHOWN, which is not the user declining it. The
    // common cause is `SecurityError`: `showSaveFilePicker` requires transient
    // user activation (~5 s in Chrome) and the bundle fetch above can outlive
    // it — exactly what happens on the large projects the 180 s timeout exists
    // to serve. Rethrowing discarded bytes already in hand and showed a
    // browser-internals string ("Must be handling a user gesture to show a
    // file picker") with no file and no way to succeed on retry, because the
    // retry hits the same wall.
    //
    // The anchor needs no activation, so fall back to it: the file lands in
    // the browser's default download folder rather than a chosen one. A worse
    // location beats no file. Nothing has been written at this point, so there
    // is no half-saved file to contradict.
    appLog('WARN', `Save dialog unavailable (${(e as Error)?.name ?? 'unknown'}) — downloading to the default folder instead`)
    return anchorDownload(blob, name)
  }

  // Deliberately NOT covered by the fallback above: if the write fails the
  // user has a file at their chosen location that is empty or truncated, and
  // quietly downloading a second copy while reporting success would hide that.
  // Let it reach the caller.
  const writable = await handle.createWritable()
  await writable.write(blob)
  await writable.close()
  if (!skipCache) {
    _bundleHandles.set(name, handle)
  }
  return 'picker'
}

// Save an already-fetched blob via a download anchor. No File System Access
// API, no user activation required. Used both when the browser has no picker
// (Firefox, Safari, and the pywebview WKWebView shell) and as the fallback
// when the picker cannot be shown.
function anchorDownload(blob: Blob, name: string): BundleSaveResult {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${name}.pypsaproj.zip`
  a.click()
  URL.revokeObjectURL(url)
  return 'download'
}

// Forget the cached file handle for a project — call when a project is
// deleted or renamed so the next save doesn't write to a stale location.
export function forgetBundleLocation(name: string): void {
  _bundleHandles.delete(name)
}

// `saveBlobToDisk` was removed here in phase 2a Task 6. It had zero callers —
// verified across every ref in the repo, not just this branch — and it was a
// second download path competing with the anchor every other export already
// uses. Its `showSaveFilePicker` branch does not exist in WKWebView, so in the
// desktop shell it could only ever have taken its own fallback.
//
// If a blob save is needed again, `downloadProjectBundle` above is the shape
// to copy: fetch through the axios client FIRST, then save the bytes. Saving
// straight from a URL with `<a download>` cannot see a status code, so a
// 401/403/404 body gets written to disk as a valid-looking file.

// Reset the in-memory backend network and clear the cached diagram layout.
// The diagram state is now keyed per-project (`network-diagram:<project>:state`);
// a reset unbinds the network (→ the `__local__` slot), so sweep EVERY matching
// key rather than a single one — that way a reset can't leave any stale
// per-project diagram state (waypoints/positions) behind to bleed into a
// freshly created/loaded network.
export async function resetBackendNetwork(): Promise<void> {
  await networkApi.resetNetwork()
  try {
    const keys: string[] = []
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i)
      if (k && DIAGRAM_STATE_KEY_RE.test(k)) keys.push(k)
    }
    keys.forEach(k => localStorage.removeItem(k))
  } catch { /* noop */ }
}
