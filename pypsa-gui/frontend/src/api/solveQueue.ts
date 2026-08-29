import client from './client'

// One queued solve of a saved project. Mirrors the backend SolveJob.to_public
// (services/solve_queue.py). `position` is the 1-based place in the queue for a
// still-queued job, null once it's running/terminal.
// `interrupted`: the backend process died while this job was running and nobody
// stopped it (services/solve_job_store.reconcile_on_boot). Terminal, and
// deliberately NOT the same word as `aborted`, which means a user decided.
export type SolveJobStatus =
  'queued' | 'running' | 'completed' | 'failed' | 'aborted' | 'interrupted'

export interface SolveJob {
  // UUID string. Was a per-process integer that collided across replicas.
  id: string
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
  // Whether `POST /{id}/dismiss` would ACCEPT this row from THIS caller —
  // terminal AND queued by them. Computed server-side (routers/solve_queue.py)
  // because dismissal is owner-gated on `enqueued_by_user_id`, which the
  // payload deliberately does not carry: emitting the owner id would let any
  // caller enumerate which colleague queued which job. A capability answers
  // the only question a client has and discloses nothing about anyone else.
  //
  // Render the Dismiss control from this, never from `isTerminal(job)` alone —
  // that is the "control must match the route" rule this file's siblings apply
  // to Clear finished, and getting it wrong here means a guaranteed 403 on
  // every row a colleague queued.
  can_dismiss: boolean
}

export interface QueueList {
  jobs: SolveJob[]
  // Ids of the jobs solving right now. Replaces the scalar `current`, which
  // could not represent a pool and reported one arbitrary running job.
  running: string[]
  // The dispatcher is paused: running jobs finish, nothing else starts.
  paused: boolean
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
  // `already_queued: true` means the server returned the EXISTING job for an
  // idempotent re-enqueue (200, not 409 — routers/solve_queue.py) and created
  // nothing. Callers must not report "queued" for that case.
  enqueue: (projectId: string) =>
    client.post<SolveJob & { already_queued: boolean }>(
      '/simulation/queue', { project_id: projectId },
    ).then(r => r.data),
  abort: (jobId: string) =>
    client.post<SolveJob>(`/simulation/queue/${jobId}/abort`).then(r => r.data),
  clearFinished: () =>
    client.post<{ removed: number }>('/simulation/queue/clear_finished').then(r => r.data),
  // ── increment 3: the five routes that shipped without a client ──────────
  //
  // All five are on the foreign-lock gate's exemption allowlist
  // (`backend/main.py`). That is load-bearing rather than incidental: they sit
  // under the gated `/api/simulation/` prefix, and without an entry each is
  // refused 409 `project_locked` whenever the caller's ACTIVE project happens
  // to be held by someone else — a project none of them resolves. Adding a
  // sixth route here means adding its allowlist entry in the same change.
  //
  // Pause and resume act on the ONE process-global dispatcher, so they are
  // gated server-side on `is_super_admin` (local mode exempt, which is why the
  // desktop app can always use them). Gate the control on the RAW flag, not on
  // `useAuth().isAdmin` — see `can_dismiss` above for the same trap.
  pause: () =>
    client.post<{ paused: boolean }>('/simulation/queue/pause').then(r => r.data),
  resume: () =>
    client.post<{ paused: boolean }>('/simulation/queue/resume').then(r => r.data),
  // Cancels every QUEUED job this caller could have cancelled one at a time,
  // each authorized by the same predicate as the single-job abort. RUNNING
  // jobs are deliberately out of scope — stopping a live solve wastes minutes
  // of solver time and is what the per-row abort is for. `cancelled` counts
  // only what the caller actually cancelled, so 0 is a legitimate answer and
  // the number never hints at anyone else's work.
  cancelQueued: () =>
    client.post<{ cancelled: number }>('/simulation/queue/cancel_queued').then(r => r.data),
  // Run a finished job again as a NEW queued job, inheriting the ORIGINAL
  // config snapshot — "run that again" means that run. All four terminal
  // statuses are eligible, `interrupted` included. 409 if the job is still
  // queued/running, 409 `project_locked` if another user holds the project's
  // edit lock, 404 if the project no longer has a saved network.
  requeue: (jobId: string) =>
    client.post<SolveJob & { already_queued: boolean }>(
      `/simulation/queue/${jobId}/requeue`,
    ).then(r => r.data),
  // Hide a finished job from THIS caller's listing only; it stays in everyone
  // else's. Gate the control on the row's `can_dismiss`.
  dismiss: (jobId: string) =>
    client.post<{ dismissed: true }>(`/simulation/queue/${jobId}/dismiss`).then(r => r.data),
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
  jobLogHistory: (jobId: string) =>
    client.get<{ lines: string[]; status: SolveJobStatus }>(
      `/simulation/queue/${jobId}/log_history`,
    ).then(r => r.data),
  // EventSource takes an absolute app path, not the axios base, so this is a
  // URL builder rather than a request.
  jobLogStreamUrl: (jobId: string) => `/api/simulation/queue/${jobId}/log_stream`,
}

export const TERMINAL_STATUSES: ReadonlySet<SolveJobStatus> =
  new Set(['completed', 'failed', 'aborted', 'interrupted'])
export const ACTIVE_STATUSES: ReadonlySet<SolveJobStatus> = new Set(['queued', 'running'])

export function isActive(j: SolveJob): boolean {
  return ACTIVE_STATUSES.has(j.status)
}

export function isTerminal(j: SolveJob): boolean {
  return TERMINAL_STATUSES.has(j.status)
}
