import axios from 'axios'
import client from './client'
import type {
  ProjectInfo, ImportSummary, SaveResult, BundleImportResult,
  CompareState, ResultsSummary,
} from './types'

// Recover the server's error message from a request made with
// `responseType: 'blob'`.
//
// axios hands back `err.response.data` as a Blob for those, so every ordinary
// `data?.detail` lookup — including the one in the client's error interceptor
// — silently misses and falls back to the generic "Request failed with status
// code 404". The reason the server actually gave is sitting inside the Blob.
export async function bundleErrorMessage(e: unknown): Promise<string> {
  const data = (e as { response?: { data?: unknown } })?.response?.data
  if (data instanceof Blob) {
    try {
      const parsed = JSON.parse(await data.text())
      if (typeof parsed?.detail === 'string') return parsed.detail
    } catch {
      // Not JSON (an HTML error page, or a truncated body). Fall through to
      // the axios message rather than showing the user raw markup.
    }
  }
  return String((e as Error)?.message ?? e)
}

// Snapshot — point-in-time copy of a project bundle. Returned by the
// /api/projects/{name}/snapshots endpoints.
export interface SnapshotInfo {
  id: string
  label: string
  message: string
  created_at: string
  bus_count: number
  snapshot_count: number
  objective: number | null
  has_results: boolean
  // 'fresh' — dispatch tables match the snapshot's topology (true SOLVED).
  // 'stale' — dispatch is for a different generator/bus set than what's
  //   actually in the snapshot. Typical cause: user edited topology after
  //   the prior solve but before this snapshot was taken. UI should show
  //   a STALE badge instead of SOLVED — the numbers in the dispatch tables
  //   would be misleading if restored as-is.
  // 'none'  — no dispatch tables at all (truly unsolved).
  dispatch_status?: 'fresh' | 'stale' | 'none'
}

export interface RestoreResult {
  restored: string
  label: string
  bus_count: number
  snapshot_count: number
}

// Multi-user edit lock (Task 8/14). `holder_email` names the current holder;
// `yours` is true when that holder is the requesting user. `null` (top-level
// `lock` field) means no lock exists on the project.
export interface ProjectLockInfo {
  holder_email: string
  yours: boolean
}

// One row of a project tree-root's membership set (Task 7/14). `email` can be
// null if the user record was removed but the membership row lingers.
export interface ProjectMember {
  user_id: string
  email: string | null
}

// ── Unclaimed (pre-multi-user) projects ───────────────────────────────────
// Projects saved to disk before tenancy existed have no owning organization,
// so they are invisible to /projects/. `listUnclaimed` surfaces them to the
// signed-in user; `importUnclaimed` adopts one root (plus its scenario
// descendants) into the caller's workspace.
export interface UnclaimedProject {
  name: string
  parent_project: string | null
  scenario_description: string | null
  // False when the bundle directory has metadata but no network file — the
  // import still succeeds, but the project opens empty.
  has_network: boolean
  descendant_names: string[]
}

export interface ImportedProjectRef {
  id: string
  name: string
  org_id: string
  parent_project_id: string | null
}

export interface ImportUnclaimedResult {
  root: ImportedProjectRef
  claimed: ImportedProjectRef[]
  warnings: string[]
}

interface ListProjectsOptions {
  rootsOnly?: boolean
}

// 404 means the backend runs with auth disabled, where "unclaimed" has no
// meaning — the caller renders nothing rather than an error.
function listUnclaimedProjects(): Promise<UnclaimedProject[]> {
  return client
    .get<UnclaimedProject[]>('/projects/unclaimed', { skipErrorToast: true })
    // A non-array body means `/unclaimed` got shadowed by the dynamic
    // `GET /projects/{name}` route — treat it as "nothing to import".
    .then(r => (Array.isArray(r.data) ? r.data : []))
    .catch((error: unknown) => {
      if (axios.isAxiosError(error) && error.response?.status === 404) return []
      throw error
    })
}

function listProjects(): Promise<ProjectInfo[]>
function listProjects(options: ListProjectsOptions): Promise<ProjectInfo[]>
function listProjects(options?: ListProjectsOptions): Promise<ProjectInfo[]> {
  return client.get<ProjectInfo[]>('/projects/', {
    params: options?.rootsOnly ? { roots_only: true } : undefined,
  }).then((r) => {
    const projects = r.data
    return options?.rootsOnly
      ? projects.filter(project => project.parent_project == null)
      : projects
  })
}

export const projectsApi = {
  list: listProjects,
  listUnclaimed: listUnclaimedProjects,
  // 400/403/409 carry a `{detail}` the caller renders inline next to the row,
  // so the global error toast is suppressed.
  importUnclaimed: (name: string) =>
    client.post<ImportUnclaimedResult>(
      `/projects/unclaimed/${encodeURIComponent(name)}/import`,
      null,
      { skipErrorToast: true },
    ).then(r => r.data),
  // Phase 8 scenarios. A scenario is a normal project with metadata
  // `parent_project=<base>`. `createScenario` saves the current in-memory
  // network to a new project keyed by `name`, branched off `base`. Caller
  // decides whether to switch — the new scenario does NOT auto-activate.
  createScenario: (base: string, name: string, description?: string) =>
    client.post<ProjectInfo>(
      `/projects/${encodeURIComponent(base)}/scenarios`,
      { name, description: description ?? null },
    ).then(r => r.data),
  // Read-only summary of a non-active project; safe to call from anywhere
  // (doesn't touch the in-memory PyPSAService singleton).
  compareState: (name: string) =>
    client.get<CompareState>(
      `/projects/${encodeURIComponent(name)}/compare-state`,
    ).then(r => r.data),
  // Rename a project on disk (and reparent any child scenarios). Returns
  // the new ProjectInfo at the new path. The caller is responsible for
  // updating `currentProject` in the store and invalidating React-Query
  // caches keyed by the old name (['projects'], ['meta'], ['compare-state',
  // oldName], ['results-summary', oldName], …).
  rename: (oldName: string, newName: string) =>
    client.post<ProjectInfo>(
      `/projects/${encodeURIComponent(oldName)}/rename`,
      { new_name: newName },
    ).then(r => r.data),
  // Per-tab summary for the Compare Scenarios v2 view. Loads the project's
  // netcdf into a transient pypsa.Network, computes per-category aggregates
  // (capacity, dispatch, …) with per-period breakdowns, then discards.
  // Each phase fills in more category fields on the same payload.
  resultsSummary: (name: string) =>
    client.get<ResultsSummary>(
      `/projects/${encodeURIComponent(name)}/results-summary`,
    ).then(r => r.data),
  // Blank-canvas layout ("latent coordinates"): node positions + edge
  // waypoints for the schematic view. Stored as `layout.json` in the
  // project bundle — travels with the project, but is NOT part of the
  // network model (decoupled from the geographic bus.x/y the map view
  // and clustering use). `getLayout` returns `{}` when none saved yet.
  // The payload is opaque on the wire; the canvas owns its shape.
  getLayout: (name: string) =>
    client.get<Record<string, unknown>>(
      `/projects/${encodeURIComponent(name)}/layout`,
    ).then(r => r.data),
  putLayout: (name: string, layout: Record<string, unknown>) =>
    client.put<{ saved: string }>(
      `/projects/${encodeURIComponent(name)}/layout`, layout,
    ).then(r => r.data),
  // `clearUndo` defaults to true (matches the backend default) so explicit
  // user saves drop the undo stack. Autosave passes clearUndo=false to keep
  // recent revert history available across background snapshots.
  //
  // `expect` is the project the caller believes is the ACTIVE/loaded one. When
  // provided, the backend refuses (409) if its in-memory network is actually
  // bound to a different project — the atomic, server-side guard against the
  // cross-project overwrite that destroyed a project on 2026-05-28. Callers
  // saving the active project (autosave, explicit Ctrl+S) pass it; callers
  // that intentionally write under a different name (Save a Copy, first save
  // of a new project) omit it.
  // `rebind=true` makes the backend treat `name` as the loaded project after
  // the save — for flows that save under a NEW name and then make it active
  // (Save-As, clone, first save). Without it, the backend stays bound to the
  // prior project and every subsequent save of the new name 409s until reload.
  // Save-a-Copy leaves it false so the original stays the active binding.
  save: (name: string, force = false, clearUndo = true, expect?: string, rebind = false) =>
    client.post<SaveResult>(`/projects/${encodeURIComponent(name)}`, null, {
      params: {
        ...(force ? { force: true } : {}),
        ...(clearUndo ? {} : { clear_undo: false }),
        ...(expect ? { expect } : {}),
        ...(rebind ? { rebind: true } : {}),
      },
    }).then(r => r.data),
  load: (name: string) => client.get<ImportSummary>(`/projects/${encodeURIComponent(name)}`).then(r => r.data),
  // B8 instant in-memory switch: make `name` the active backend context. No
  // destructive round-trip when the project is already resident (pure pointer
  // swap server-side); a cold project is built + hydrated + registered. 409 if
  // a FOREGROUND solve is in flight on the current project; 404 unknown; 400 bad
  // id. The switch flow re-keys all reactive queries to nk(name,…) afterwards.
  // `evicted` (B9) lists project_ids the backend dropped from its resident
  // registry to stay under RESIDENT_CAP when this activate registered a new
  // context. The switch flow drops those projects' retained React Query caches
  // so the client RAM mirrors the server-side eviction.
  activate: (name: string) =>
    client.post<{ activated: string; evicted?: string[] }>(
      `/projects/${encodeURIComponent(name)}/activate`,
    ).then(r => r.data),
  // `cascade=true` deletes child scenarios recursively. Default `false` makes
  // the backend refuse (409) when the project has descendants — UI surfaces
  // a "X has N scenarios attached" prompt before retrying with cascade=true.
  // Returns `{deleted: [...names actually removed], failed: [...names whose
  // rmtree raised — e.g. a Windows file lock]}` so the caller can clear
  // `currentProject` when the active project was actually removed, and warn
  // about anything that couldn't be deleted.
  delete: (name: string, cascade = false) =>
    client.delete<{ deleted: string[]; failed: string[] }>(
      `/projects/${encodeURIComponent(name)}`,
      { params: cascade ? { cascade: true } : undefined },
    ).then(r => r.data),
  statistics: (name: string) => client.get(`/projects/${encodeURIComponent(name)}/statistics`).then(r => r.data),
  // Stream the project's full state as a .pypsaproj.zip bundle (network.nc +
  // user_ts.json + solver_config.json + metadata.json). Returns a Blob so the
  // caller can hand it to showSaveFilePicker or a download anchor.
  //
  // NOTE for error handling: because this sets `responseType: 'blob'`, a
  // failure arrives with `err.response.data` as a *Blob*, so neither the axios
  // interceptor nor a caller can read `detail` off it directly. Use
  // `bundleErrorMessage` to recover the server's reason.
  downloadBundle: (name: string, opts?: { skipErrorToast?: boolean }) =>
    client.get<Blob>(`/projects/${encodeURIComponent(name)}/bundle`, {
      responseType: 'blob',
      // Same reason `importBundle` below overrides it, for the same artifact:
      // the backend builds the entire zip in memory before sending a byte, and
      // for a year-of-hourly-data project that exceeds the 30 s default. This
      // was previously served by a plain download anchor, which has no timeout
      // at all — so inheriting the default here would make big projects fail
      // where they used to work.
      timeout: 180_000,
      skipErrorToast: opts?.skipErrorToast,
    }).then(r => r.data),
  // Create a new project from a bundled starter network (backend/project_
  // templates/<id>). The backend copies the template's network.nc into a
  // fresh project dir, loads it, and returns the same shape as importBundle.
  // `name` is optional — the backend defaults to the template's friendly name
  // and uniquifies on collision.
  createFromTemplate: (templateId: string, name?: string) =>
    client.post<BundleImportResult>(
      `/projects/from_template/${encodeURIComponent(templateId)}`,
      null,
      { params: name ? { name } : undefined },
    ).then(r => r.data),
  importBundle: (file: File, targetName?: string) => {
    const fd = new FormData()
    fd.append('file', file)
    const params = targetName ? { name: targetName } : undefined
    // Bundle imports parse user_ts.json and reindex multiple time-series tables
    // — for a year-of-hourly-data project this can take >30 s. Override the
    // default axios timeout so a slow-but-successful import isn't aborted.
    return client.post<BundleImportResult>('/projects/import_bundle', fd, {
      params,
      timeout: 180_000,
    }).then(r => r.data)
  },
  // ── Snapshots ───────────────────────────────────────────────────────────
  // Snapshots are point-in-time copies of a project's on-disk bundle. They
  // operate on the FILES, not the in-memory network — so users must Save
  // first if they want a snapshot to include unsaved edits.
  listSnapshots: (name: string) =>
    client.get<SnapshotInfo[]>(`/projects/${encodeURIComponent(name)}/snapshots`)
      .then(r => r.data),
  createSnapshot: (name: string, label: string, message = '') =>
    client.post<SnapshotInfo>(
      `/projects/${encodeURIComponent(name)}/snapshots`,
      { label, message },
    ).then(r => r.data),
  restoreSnapshot: (name: string, snapshotId: string) =>
    client.post<RestoreResult>(
      `/projects/${encodeURIComponent(name)}/snapshots/${encodeURIComponent(snapshotId)}/restore`,
    ).then(r => r.data),
  deleteSnapshot: (name: string, snapshotId: string) =>
    client.delete(
      `/projects/${encodeURIComponent(name)}/snapshots/${encodeURIComponent(snapshotId)}`,
    ),

  // ── Multi-user edit lock (auth mode only; 404 otherwise) ──────────────────
  // `id` may be a project UUID or name — the backend's resolver accepts either.
  // Acquire/heartbeat use `skipErrorToast` because a 409 (someone else holds
  // the lock, or ours expired) is EXPECTED and surfaced as the read-only
  // banner, not as an error toast; the structured detail carries the current
  // holder in `.detail.lock`.
  acquireLock: (id: string) =>
    client.post<{ lock: ProjectLockInfo | null }>(
      `/projects/${encodeURIComponent(id)}/lock`, null, { skipErrorToast: true },
    ).then(r => r.data),
  heartbeatLock: (id: string) =>
    client.post<{ lock: ProjectLockInfo | null }>(
      `/projects/${encodeURIComponent(id)}/lock/heartbeat`, null, { skipErrorToast: true },
    ).then(r => r.data),
  releaseLock: (id: string) =>
    client.delete<{ released: boolean }>(
      `/projects/${encodeURIComponent(id)}/lock`, { skipErrorToast: true },
    ).then(r => r.data),

  // ── Membership (auth mode only; server resolves the id to the tree ROOT) ──
  getMembers: (id: string) =>
    client.get<ProjectMember[]>(
      `/projects/${encodeURIComponent(id)}/members`,
    ).then(r => r.data),
  setMembers: (id: string, userIds: string[]) =>
    client.put<ProjectMember[]>(
      `/projects/${encodeURIComponent(id)}/members`, { user_ids: userIds },
    ).then(r => r.data),
}
