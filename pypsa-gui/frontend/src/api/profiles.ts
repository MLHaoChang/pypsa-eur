import { AxiosError } from 'axios'
import client from './client'

// The renewable profile generator's HTTP surface (backend Task 7,
// routers/profiles.py). Two shapes:
//   * global: presets + the synthetic, downsampled estimate preview
//   * project-scoped: generate (a solve-queue job of kind profile_gen),
//     pending artifacts, apply onto a generator's p_max_pu
// Errors surface through `errorText`, which reads the backend's typed
// `{error_kind, message}` detail objects as well as plain strings.

export type ProfileTechnology = 'wind' | 'solar'
export type ProfileSource = 'synthetic' | 'atlite'
// C2: aggregation happens at generation (backend ruling D1=A); annual was
// dropped (D4=B) — an annual mean is a scalar, not a profile.
export type ProfileResolution = 'hourly' | 'daily' | 'weekly'

export interface ProfilePoint {
  id: string
  lon: number
  lat: number
}

export interface ProfilePreset {
  name: string
  technology: ProfileTechnology
  description: string
  params: Record<string, unknown>
}

export interface ProfileEstimate {
  estimate: true
  source: 'synthetic'
  technology: ProfileTechnology
  weather_year: number
  resolution: ProfileResolution
  timestamps: string[]
  series: Record<string, number[]>
}

export interface PendingProfile {
  job_id: string
  project_id?: string
  technology: ProfileTechnology
  source: ProfileSource
  preset: string
  weather_year: number
  // Optional: artifacts generated before C2 carry no resolution (treat as
  // hourly).
  resolution?: ProfileResolution
  bbox: [number, number, number, number]
  generated_at: string
  points: string[]
  point_coords?: Record<string, [number, number]>
}

export interface PendingList {
  pending: PendingProfile[]
  jobs: Array<{ id: string; status: string }>
  keep: number
}

export interface GenerateSpec {
  points: ProfilePoint[]
  technology: ProfileTechnology
  preset?: string
  params?: Record<string, unknown>
  source?: ProfileSource
  year?: number
  resolution?: ProfileResolution
}

export interface ApplyResult {
  component: string
  attribute: 'p_max_pu'
  hours: number
  weather_year: number
  leap_day: string
  tiled: boolean
  periods?: number[]
  resolution: ProfileResolution
  dispatch_cleared: boolean
  mean: number
  peak: number
  // Set when the component's bus moved more than 0.5° since generation —
  // applied anyway, but the user should know the weather is for elsewhere.
  warning?: string
  // What the snapshots were read as ("UTC", an IANA zone, or
  // "UTC (assumed; site clock unknown)" with a matching warning).
  time_basis?: string
  warnings?: string[]
}

export function errorText(e: unknown): string {
  const data = (e as AxiosError)?.response?.data as { detail?: unknown } | undefined
  const detail = data?.detail
  if (detail == null) return (e as Error)?.message ?? 'Unknown error'
  if (typeof detail === 'string') return detail
  if (typeof detail === 'object' && 'message' in (detail as object)) {
    return String((detail as { message: unknown }).message)
  }
  return JSON.stringify(detail)
}

export const profilesApi = {
  presets: (technology?: ProfileTechnology) =>
    client.get<{ presets: ProfilePreset[] }>('/profiles/presets', {
      params: technology ? { technology } : {},
    }).then(r => r.data.presets),

  estimate: (spec: GenerateSpec) =>
    client.post<ProfileEstimate>('/profiles/estimate', spec).then(r => r.data),

  generate: (project: string, spec: GenerateSpec) =>
    client.post<{ id: string; status: string; kind: string }>(
      `/projects/${encodeURIComponent(project)}/profiles/generate`, spec,
    ).then(r => r.data),

  pending: (project: string) =>
    client.get<PendingList>(
      `/projects/${encodeURIComponent(project)}/profiles/pending`,
    ).then(r => r.data),

  apply: (project: string, jobId: string, component: string, pointId?: string) =>
    client.post<ApplyResult>(
      `/projects/${encodeURIComponent(project)}/profiles/apply`,
      { job_id: jobId, component, point_id: pointId ?? null },
    ).then(r => r.data),
}

// ── Generic time-series generators (TS 01–04) ─────────────────────────────
// Every family writes a pending artifact; `seriesApi.apply` writes it to the
// target its family (or meta) states, behind the same undo as the renewable
// apply. A refinement turns one pending artifact into another.

export interface LoadArchetype {
  name: string
  params: Record<string, unknown>
}

export interface LoadPreset {
  name: string
  archetype: string
  params: Record<string, unknown>
}

export interface SeriesStats {
  peak_mw?: number
  annual_mwh?: number
  // What this model's snapshots will count (Σ value × weighting) — a sampled
  // template sees less than the year. null when apply could not align it.
  modelled_mwh?: Record<string, number> | null
  min?: number
  mean?: number
  max?: number
}

/** One calendar week of what apply would write (the routes' dry run): the
 *  week holding the series' peak, or its lowest hour for an availability
 *  cap, beside the component's current series for those snapshots. */
export interface PreviewWeek {
  label: 'peak week' | 'lowest week'
  period: number | null
  snapshots: string[]
  new: number[]
  current: number[] | null
}

export interface PendingSeries {
  job_id: string
  family: string
  points: string[]
  provenance?: string
  stats: Record<string, SeriesStats | Array<Record<string, number | string>>>
  warnings?: string[]
  unmatched?: string[]
  qa_warnings?: Array<{ column: string; code: string; message: string }>
  refinements?: Array<Record<string, unknown>>
  weeks?: Record<string, PreviewWeek>
  [k: string]: unknown
}

export interface SeriesApplyResult {
  component: string
  component_type: string
  attribute: string
  peak: number
  mean: number
  min: number
  profile_annual_mwh?: number
  modelled_mwh?: Record<string, number>
  warnings: string[]
}

export type RefineOp = Record<string, unknown> & { op: string }

const projectPath = (project: string) => `/projects/${encodeURIComponent(project)}/profiles`

export const seriesApi = {
  loadArchetypes: () =>
    client.get<{ archetypes: LoadArchetype[]; presets: LoadPreset[] }>(
      '/profiles/load-archetypes').then(r => r.data),

  generateLoad: (project: string, body: {
    archetype: string; loads: string[]; params: Record<string, unknown>; year?: number
  }) => client.post<PendingSeries>(`${projectPath(project)}/loads/generate`, body).then(r => r.data),

  uploadLoad: (project: string, file: File, opts: {
    unit?: string; timezone?: string; year?: number; annual_mwh?: number
  }) => {
    const form = new FormData()
    form.append('file', file)
    for (const [k, v] of Object.entries(opts)) {
      if (v !== undefined && v !== '' && v !== null) form.append(k, String(v))
    }
    return client.post<PendingSeries>(`${projectPath(project)}/loads/upload`, form)
      .then(r => r.data)
  },

  generateAvailability: (project: string, body: {
    kind: string; components: string[]; component_type: 'generators' | 'links'
    params: Record<string, unknown>; year?: number
  }) => client.post<PendingSeries>(`${projectPath(project)}/availability/generate`, body)
    .then(r => r.data),

  generateSeries: (project: string, body: {
    kind: string; components: string[]; params: Record<string, unknown>; year?: number
  }) => client.post<PendingSeries>(`${projectPath(project)}/series/generate`, body).then(r => r.data),

  refine: (project: string, jobId: string, ops: RefineOp[]) =>
    client.post<PendingSeries>(`${projectPath(project)}/refine`, { job_id: jobId, ops })
      .then(r => r.data),

  apply: (project: string, body: {
    job_id: string; component: string; component_type?: string; attribute?: string
  }) => client.post<SeriesApplyResult>(`${projectPath(project)}/apply`, body).then(r => r.data),
}
