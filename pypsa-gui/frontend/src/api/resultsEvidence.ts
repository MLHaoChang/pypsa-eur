import { client } from './client'
import type { ResultsSourceBinding } from '../utils/resultsSource'

export interface BranchLoadingRow {
  name: string
  asset_key?: string
  name_truncated?: boolean
  class: 'Line' | 'Transformer'
  basis: 'active_power_proxy' | 'ac_apparent_power'
  util_pct: number | null
  rating_pct: number | null
  mean_util_pct: number | null
  peak_flow: number | null
  mean_flow: number | null
  peak_at: string
  h_at_limit: number | null
  h_over_limit: number | null
  rating: { value: number | null; basis: 'configured' | 'optimized' }
  capacity?: { configured: number | null; optimized: number | null; extendable: boolean }
  s_max_pu: { min: number | null; max: number | null }
  terminals?: string
  snapshots_with_data?: number
}
export interface LoadflowView {
  status: 'ok' | 'unavailable'
  unavailable_kind?: string
  message?: string
  binding?: ResultsSourceBinding
  rows?: BranchLoadingRow[]
  rows_truncated?: boolean
  skipped?: Array<{ name: string; class: string; reason: string }>
  skipped_truncated?: boolean
  missing_evidence?: string[]
  series?: Array<{ name: string; class: string; asset_key?: string; name_truncated?: boolean; flow: Array<number | null>; util_pct: Array<number | null> }>
  series_index?: string[]
  series_truncated?: boolean
  window?: { from: number; to: number; weighted_hours: number | null; snapshots: number }
  claims?: { full_ac_thermal_compliance: false; reason: string }
}
export interface RunView {
  status: 'ok'
  binding: ResultsSourceBinding
  data: {
    run_id: string
    status: string
    started_at?: string | null
    fingerprint?: string
    metrics: Record<string, { value: number | null; unit: string }>
    sections?: Array<{ name: string; available: boolean }>
    section?: string
    data?: unknown
    warnings?: string[]
    error?: unknown
  }
  baseline?: unknown
}
export const resultsEvidenceApi = {
  loadflow: (source: 'lopf' | 'ac_pf', from?: number, to?: number) => client.get<LoadflowView>(
    '/results/loadflow-view', { params: { source, window_from: from, window_to: to } }).then(r => r.data),
  run: (runId: string, section?: string, baselineRunId?: string) => client.get<RunView>(
    '/results/run-view', { params: { run_id: runId, section, baseline_run_id: baselineRunId }, skipErrorToast: true }).then(r => r.data),
  /** One past run's kept evidence as a file: the same bytes and name the
   *  assistant's export_run_evidence writes (LC-03 past-run parity). */
  runFile: (runId: string, section?: string) => client.get<Blob>(
    '/results/run-evidence.json', { params: { run_id: runId, section }, responseType: 'blob', skipErrorToast: true })
    .then(r => ({ blob: r.data, filename: fileNameOf(r.headers['content-disposition'])
      ?? (section ? `run_${runId}_${section}.json` : `run_${runId}.json`) })),
}

function fileNameOf(disposition: unknown): string | null {
  if (typeof disposition !== 'string') return null
  const star = /filename\*=UTF-8''([^;]+)/i.exec(disposition)
  if (star) return decodeURIComponent(star[1])
  return /filename="?([^";]+)"?/i.exec(disposition)?.[1] ?? null
}

/** Why a past run's file download was refused, in the server's words: a blob
 *  response carries its JSON detail as a Blob. The LC03-ACCESS-1 sentence when
 *  access is gone; a plain fallback for anything else. */
export async function runFileRefusal(error: unknown): Promise<string> {
  const data = (error as { response?: { data?: unknown } } | null)?.response?.data
  if (data instanceof Blob) {
    try {
      const reason = projectUnavailableReason({ response: { data: JSON.parse(await data.text()) } })
      if (reason) return reason
    } catch { /* not JSON: the fallback below */ }
  }
  return projectUnavailableReason(error)
    ?? "This run's kept results could not be downloaded. Current results were not substituted."
}

/** The server's own words when a run read refuses because the project is no
 * longer available to this user (LC-T08), or none is open for them now, as after
 * access loss (LC03-ACCESS-1); null for any other failure. */
export function projectUnavailableReason(error: unknown): string | null {
  const detail = (error as { response?: { data?: { detail?: { error_kind?: string; message?: string } } } } | null)
    ?.response?.data?.detail
  const kind = detail?.error_kind
  return (kind === 'project_unavailable' || kind === 'run_project_required') && detail?.message
    ? detail.message : null
}
