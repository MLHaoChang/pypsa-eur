// The planning → dynamics pipeline (gridspine), `/api/gridspine/*`.
//
// Every thunk here maps to ONE handler in `backend/routers/gridspine.py`, which
// is itself a wrapper over one function in `services/gridspine_service.py` —
// the same functions the copilot's `gridspine_*` tools call. So this module is
// the UI's half of the spec's parity rule and nothing more: no client-side
// derivation of state that the backend already reports.
//
// Status, snapshots and ledger are polled or re-read while a study runs, and a
// capacity-expansion project answers 409 to all of them by design (the panel
// renders that inline), so those GETs pass `skipErrorToast` — a toast per poll
// would be the wrong surface for an expected answer.
import client from './client'

export type StageName = 'ingest' | 'dispatch' | 'ranking' | 'loadflow' | 'screening' | 'handoff'
export const STAGES: readonly StageName[] = ['ingest', 'dispatch', 'ranking', 'loadflow', 'screening', 'handoff']

export type StageState = 'pending' | 'running' | 'done' | 'failed' | 'aborted'
export type StudyStatus = 'not started' | 'running' | 'completed' | 'failed' | 'aborted'

export interface StudyConfig {
  hours: number
  k: number
  window: number
  overlap: number
  screen: boolean
  n2_prune_threshold_pct: number
  from_dispatch: string | null
  // A solved project's saved network.nc as the dispatch source (increment 5,
  // D3). The backend stores the path and, when it can, names the project.
  from_network?: string | null
  from_project?: string | null
  outdir?: string
  templates_overlay?: string | null
}

/** Where the next run's dispatch comes from — one of three, never two. */
export type DispatchSource =
  | { kind: 'generate' }
  | { kind: 'from_dispatch'; dir: string }
  | { kind: 'from_project'; project: string }

function dispatchSourceBody(source: DispatchSource) {
  switch (source.kind) {
    case 'generate': return { source: 'generate' }
    case 'from_dispatch': return { source: 'from_dispatch', from_dispatch: source.dir }
    case 'from_project': return { source: 'from_project', from_project: source.project }
  }
}

/** Any subset of the editable config. Present `from_dispatch` (even `null`,
 *  meaning "generate") is sent explicitly; absent means "leave as is". */
export type StudyConfigPatch = Partial<Pick<StudyConfig,
  'hours' | 'k' | 'window' | 'overlap' | 'screen' | 'n2_prune_threshold_pct' | 'from_dispatch'>>

export interface StageError { stage: string; cause: string; element_ids: string[] }

/** The short form of a read-back, per bundle hour, carried by the status. */
export interface ReadbackShort {
  pass: boolean
  bus: { n: number; n_ok: number } | null
  branches: { n: number; n_ok: number } | null
}

export interface StageStatus {
  status: StudyStatus
  resumable: boolean
  error: StageError | null
  selected_hours: number[]
  converged_hours: number[]
  bundles: Record<string, string>
  stages: Record<StageName, { state: StageState; done: number; total: number }>
  // Spec stage 6 (increment 6): hours whose PowerFactory export has been read
  // back, with the verdict. Absent on a status written before the field.
  readback?: Record<string, ReadbackShort>
}

/** `readback.json`, as the backend returns it per hour. */
export interface ReadbackSummary {
  hour: number
  pass: boolean
  bus: { n: number; n_ok: number; max_vm_rel_err: number; max_va_abs_err_deg: number; worst: string; pass: boolean }
  branches: { n: number; n_ok: number; max_p_rel_err: number; max_q_abs_err_mvar: number; worst: string[]; pass: boolean } | null
  tolerances: { bus: Record<string, number>; branches: Record<string, number> }
  sources: { bus_csv: { filename: string; sha256: string; bytes: number } | null; branch_csv: { filename: string; sha256: string; bytes: number } | null }
  at: string
}

export type FigureName = 'vm' | 'va' | 'branch_p' | 'branch_q'

export interface ReadbackFigure {
  available: boolean
  name: FigureName
  hour: number
  reason?: string
  tolerance?: Record<string, number>
  rows?: { element: string; pandapower: number; powerfactory: number; err: number; ok: boolean }[]
}

export interface RankedSnapshot {
  hour: number
  reasons: string[]
  converged: boolean
  load_mw: number
  import_mw: number
  inertia_mws: number
  inertia_excl_equiv_mws: number
  ibr_share: number
  n1_severity_dc: number
  n1_severity_ac: number
}

export interface TemplateEdit {
  unit_id: string; param: string; value: number; source: string; edited_by: 'user' | 'chat'; at: string
}

export interface Ledger {
  entries: string[]
  provenance_counts: { measured: number; datasheet: number; assumed: number }
  measurements: Record<string, unknown>
  hour: number | null
  from_run?: boolean
  edits: TemplateEdit[]
}

export interface CreateStudyResponse {
  id: string
  name: string
  kind: 'planning_dynamics'
  config: StudyConfig
  status: StageStatus
}

/** The public view of a solve-queue job (`SolveJob.to_public`). */
export interface StudyJob {
  id: string
  project_id: string | null
  kind: 'solve' | 'gridspine'
  status: string
  position: number | null
}

const quiet = { skipErrorToast: true } as const

export const gridspineApi = {
  createStudy: (name: string, config: Partial<StudyConfig>) =>
    client.post<CreateStudyResponse>('/gridspine/projects', { name, config }, quiet).then(r => r.data),

  setDispatchSource: (project: string, source: DispatchSource) =>
    client.post<StudyConfig>(
      `/gridspine/${encodeURIComponent(project)}/dispatch-source`, dispatchSourceBody(source), quiet,
    ).then(r => r.data),

  /** The config the NEXT run will use. */
  config: (project: string) =>
    client.get<StudyConfig>(`/gridspine/${encodeURIComponent(project)}/config`, quiet).then(r => r.data),

  /** Change part of it. The backend refuses (409) while a job for the project
   *  is queued or running, and validates values (422) the way the wizard does. */
  updateConfig: (project: string, patch: StudyConfigPatch) => {
    const { from_dispatch, ...rest } = patch
    const body = 'from_dispatch' in patch
      ? { ...rest, from_dispatch: from_dispatch ?? null, set_from_dispatch: true }
      : rest
    return client.put<StudyConfig>(`/gridspine/${encodeURIComponent(project)}/config`, body, quiet).then(r => r.data)
  },

  run: (project: string) =>
    client.post<StudyJob>(`/gridspine/${encodeURIComponent(project)}/run`, undefined, quiet).then(r => r.data),

  status: (project: string) =>
    client.get<StageStatus>(`/gridspine/${encodeURIComponent(project)}/status`, quiet).then(r => r.data),

  snapshots: (project: string) =>
    client.get<RankedSnapshot[]>(`/gridspine/${encodeURIComponent(project)}/snapshots`, quiet).then(r => r.data),

  ledger: (project: string) =>
    client.get<Ledger>(`/gridspine/${encodeURIComponent(project)}/ledger`, quiet).then(r => r.data),

  editTemplateParam: (project: string, unitId: string, param: string, value: number, source: string) =>
    client.put<TemplateEdit>(
      `/gridspine/${encodeURIComponent(project)}/templates/${encodeURIComponent(unitId)}/${encodeURIComponent(param)}`,
      { value, source, edited_by: 'user' },
      quiet,
    ).then(r => r.data),

  /** Upload the engineer's PowerFactory export for one bundle hour: the bus
   *  CSV is required, the branch CSV optional (spec stage 6). */
  uploadReadback: (project: string, hour: number, bus: File, branches?: File | null) => {
    const fd = new FormData()
    fd.append('bus', bus)
    if (branches) fd.append('branches', branches)
    return client.post<ReadbackSummary>(`/gridspine/${encodeURIComponent(project)}/readback/${hour}`, fd, quiet).then(r => r.data)
  },

  readback: (project: string) =>
    client.get<Record<string, ReadbackSummary>>(`/gridspine/${encodeURIComponent(project)}/readback`, quiet).then(r => r.data),

  figure: (project: string, hour: number, name: FigureName) =>
    client.get<ReadbackFigure>(`/gridspine/${encodeURIComponent(project)}/figures/${hour}/${name}`, quiet).then(r => r.data),

  /** The handoff bundle for one selected hour, as a zip blob. */
  bundle: (project: string, hour: number) =>
    client.get<Blob>(`/gridspine/${encodeURIComponent(project)}/bundles/${hour}`, {
      responseType: 'blob', ...quiet,
    }).then(r => r.data),
}

/** True when the error is the backend saying "not a planning → dynamics project". */
export function isNotAStudy(err: unknown): boolean {
  const status = (err as { response?: { status?: number } } | null)?.response?.status
  return status === 409
}
