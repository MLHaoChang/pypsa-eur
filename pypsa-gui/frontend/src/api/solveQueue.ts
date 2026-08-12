import client from './client'

// One queued solve of a saved project. Mirrors the backend SolveJob.to_public
// (services/solve_queue.py). `position` is the 1-based place in the queue for a
// still-queued job, null once it's running/terminal.
export type SolveJobStatus = 'queued' | 'running' | 'completed' | 'failed' | 'aborted'

export interface SolveJob {
  id: number
  // NULLED for a job the caller may not see. `routers/solve_queue.py` redacts
  // `project_id`, `project_key` and `error` rather than dropping the row,
  // because `position` is a place in a GLOBALLY sequential queue and hiding
  // other orgs' rows would leave a caller at "#4" with one job visible.
  project_id: string | null
  // `org:uuid`. Always emitted by the backend, nulled by the same redaction.
  // Was missing from this interface entirely.
  project_key: string | null
  status: SolveJobStatus
  position: number | null
  objective: number | null
  solve_time: number | null
  condition: string | null
  // Nulled by redaction too — a failure message routinely quotes a project
  // name or a path.
  error: string | null
  enqueued_at: number
  started_at: number | null
  finished_at: number | null
}

export interface QueueList {
  jobs: SolveJob[]
  current: number | null   // id of the running job, if any
}

// Standard {index, columns, data} time-series payload (NaN→null), with an
// optional parallel `periods` array on multi-period networks.
export interface TSPayload {
  index: string[]
  columns: string[]
  data: (number | null)[][]
  periods?: (number | string)[]
}

// Disk-backed results bundle for a finished, non-active project (A6).
export interface ResultsBundle {
  available: boolean
  source: 'lopf' | 'ac_pf'
  source_available: { lopf: boolean; ac_pf: boolean }
  objective: number | null
  condition: string | null
  solve_time: number | null
  ac_pf_convergence_list: Array<{ snapshot: string; period?: number | string | null; ok: boolean }> | null
  carriers: Record<string, string>          // generator name → carrier
  generators: TSPayload | null              // generators_t.p
  storage_soc: TSPayload | null             // storage_units_t.state_of_charge
  line_loading: TSPayload | null            // lines_t.p0
}

export const solveQueueApi = {
  list: () => client.get<QueueList>('/simulation/queue').then(r => r.data),
  enqueue: (projectId: string) =>
    client.post<SolveJob>('/simulation/queue', { project_id: projectId }).then(r => r.data),
  abort: (jobId: number) =>
    client.post<SolveJob>(`/simulation/queue/${jobId}/abort`).then(r => r.data),
  clearFinished: () =>
    client.post<{ removed: number }>('/simulation/queue/clear_finished').then(r => r.data),
  // Read a finished project's dispatch straight off disk — does NOT load it
  // into the active slot. 204 → null (project exists but never solved).
  resultsBundle: (name: string, source?: 'lopf' | 'ac_pf') =>
    client.get<ResultsBundle>(
      `/projects/${encodeURIComponent(name)}/results_bundle`,
      source ? { params: { source } } : undefined,
    ).then(r => (r.status === 204 ? null : r.data)),
  // One job's log, by job id — live while it runs, retained once terminal.
  // Authorized by the same predicate as the listing, and 404s (never 403s)
  // when the caller may not see the job.
  jobLogHistory: (jobId: number) =>
    client.get<{ lines: string[]; status: SolveJobStatus }>(
      `/simulation/queue/${jobId}/log_stream`.replace('/log_stream', '/log_history'),
    ).then(r => r.data),
  // EventSource takes an absolute app path, not the axios base, so this is a
  // URL builder rather than a request.
  jobLogStreamUrl: (jobId: number) => `/api/simulation/queue/${jobId}/log_stream`,
}

export const TERMINAL_STATUSES: ReadonlySet<SolveJobStatus> = new Set(['completed', 'failed', 'aborted'])
export const ACTIVE_STATUSES: ReadonlySet<SolveJobStatus> = new Set(['queued', 'running'])

export function isActive(j: SolveJob): boolean {
  return ACTIVE_STATUSES.has(j.status)
}

export function isTerminal(j: SolveJob): boolean {
  return TERMINAL_STATUSES.has(j.status)
}
