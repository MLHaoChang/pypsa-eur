// The campus run's progress in words, shared by the campus panel (Expert) and
// the Guided "Grid checks on the campus" tile: the stage the job is at, how
// long it has run, and, when it failed, the stage it failed in and why.
import { useEffect, useState } from 'react'
import { isActiveRun, type CampusRunJob, type CampusRunStage } from '../api/campusElectrical'

/** Stage names are the backend's `RUN_STAGES`. The island checks (the
 *  frequency sweep) and the I7 cuts after them are the slow steps. */
export const STAGE_LABEL: Record<CampusRunStage, string> = {
  prepare: 'preparing the site from the saved network',
  rank: 'ranking the critical hours',
  island_checks: 'island checks: the frequency sweep at the island hours',
  cuts: 'the frequency cuts: what the failing island events would need (the slowest step)',
  size: 'sizing the transformers, the compensation and the short circuit',
  harmonic_scan: 'the harmonic impedance scan from the PCC',
  voltage_stability: 'the P–V/Q–V voltage-stability margin at the PCC',
  invest: 'buying the assets from the library, re-checked by AC load flow',
  gfm_settings: 'recommending grid-forming settings from the island hours',
  done: 'finishing',
}

/** The stage as a noun, for a sentence that says where a run failed. */
const STAGE_NAME: Record<CampusRunStage, string> = {
  prepare: 'preparing the site', rank: 'ranking the critical hours',
  island_checks: 'the island checks (frequency sweep)', cuts: 'the frequency cuts', size: 'sizing',
  harmonic_scan: 'the harmonic scan',
  voltage_stability: 'the P–V/Q–V screen',
  invest: 'the investment step', gfm_settings: 'the grid-forming settings', done: 'finishing',
}

export const stageLabel = (s: CampusRunStage | null | undefined): string => (s ? STAGE_LABEL[s] ?? s : '')

/** `m:ss` from a start (epoch s) to `toS` (default now); '' without a start. */
export function elapsed(fromS: number | null | undefined, toS?: number | null): string {
  if (fromS == null) return ''
  const s = Math.max(0, Math.round((toS ?? Date.now() / 1000) - fromS))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

/** A GFM-settings job is a campus job but not a study run. */
export const isRunJob = (j: CampusRunJob | null | undefined): j is CampusRunJob =>
  j != null && j.stage !== 'gfm_settings'

/** "step 3 of 5", when the job reports its total. */
export function stepText(job: CampusRunJob): string {
  return job.stage_total ? `step ${Math.min((job.stage_done ?? 0) + 1, job.stage_total)} of ${job.stage_total}` : ''
}

/** One line for a study run that is going on: null when it is not. */
export function runningLine(job: CampusRunJob | null | undefined): string | null {
  if (!isRunJob(job) || !isActiveRun(job)) return null
  if (job.status === 'queued') return `Waiting in the solve queue${job.position != null ? ` (#${job.position})` : ''}.`
  const step = stepText(job)
  return `Running: ${stageLabel(job.stage ?? 'prepare')}${step ? ` (${step})` : ''} · ${elapsed(job.started_at)}.`
}

/** One line for a study run that failed or was interrupted, naming its stage: null otherwise. */
export function failureLine(job: CampusRunJob | null | undefined): string | null {
  if (!isRunJob(job) || (job.status !== 'failed' && job.status !== 'interrupted')) return null
  const at = job.stage ? ` in ${STAGE_NAME[job.stage] ?? job.stage}` : ''
  const after = elapsed(job.started_at, job.finished_at)
  const how = job.status === 'failed' ? 'failed' : 'was interrupted (the backend restarted)'
  return `The last run ${how}${at}${after ? ` after ${after}` : ''}${job.error ? `: ${job.error}` : '.'}`
}

/** Re-renders every second while `active`, so an elapsed time ticks between polls. */
export function useTick(active: boolean): void {
  const [, set] = useState(0)
  useEffect(() => {
    if (!active) return
    const id = setInterval(() => set(n => n + 1), 1000)
    return () => clearInterval(id)
  }, [active])
}
