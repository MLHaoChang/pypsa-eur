import client from './client'
import type {
  ProjectInfo, ImportSummary, SaveResult, BundleImportResult,
  CompareState, ResultsSummary,
} from './types'

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

export const projectsApi = {
  list: () => client.get<ProjectInfo[]>('/projects/').then(r => r.data),
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
  downloadBundle: (name: string) =>
    client.get<Blob>(`/projects/${encodeURIComponent(name)}/bundle`, { responseType: 'blob' })
      .then(r => r.data),
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
}
