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
}

export interface ApplyResult {
  component: string
  attribute: 'p_max_pu'
  hours: number
  weather_year: number
  leap_day: string
  tiled: boolean
  periods?: number[]
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
