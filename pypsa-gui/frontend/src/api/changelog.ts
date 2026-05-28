import client from './client'

export interface ChangeLogEntry {
  id: number
  timestamp: string
  // Action verbs emitted by the backend change_log_service. Kept in sync
  // with the strings the backend actually writes; consumers that style
  // these entries (BottomPanel history tab, OverviewPanel recent activity)
  // should add a fallback for unknown verbs so a future addition doesn't
  // crash the renderer.
  //
  // - dispatch_invalidated: emitted by the undo middleware when a
  //   /api/network/* mutation cleared a previously-fresh solve.
  //   See backend/main.py::undo_snapshot_middleware.
  // - snapshot / restore / delete_snapshot: project-level snapshot ops
  //   from routers/snapshots.py.
  // - warn: orphaned-tmp recovery notifications from project load.
  action:
    | 'add' | 'update' | 'delete' | 'undo'
    | 'import' | 'export' | 'save' | 'load'
    | 'timeseries' | 'dispatch_invalidated'
    | 'snapshot' | 'restore' | 'delete_snapshot' | 'warn'
    // Phase 8 scenario-tree verbs. Backend writes both via
    // pypsa-gui/backend/routers/projects.py — `scenario_create` for
    // POST /scenarios, `delete_project` for DELETE (including cascade
    // descendants in the cascade=true path).
    | 'scenario_create' | 'delete_project' | 'cluster'
  component_type: string
  name: string
  description: string
}

export const changelogApi = {
  // Polled every 5s by the History tab — keep timeout short so a reload window
  // doesn't tie up sockets.
  getAll: () => client.get<ChangeLogEntry[]>('/changelog/', { timeout: 5000 }).then(r => r.data),
  clear:  () => client.delete('/changelog/'),
}
