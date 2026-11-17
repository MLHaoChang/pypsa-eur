/**
 * The run status line's words (run progress spec §1–§2, UX plan GO 3).
 *
 * One model for every run of the project on screen, queue job or Guided goal
 * study, so Guided and Expert say the same thing. Pure: `RunStatusLine` in
 * the run strip renders these parts and drops them in the stated order (ETA,
 * then steps and the Log link) when the window is narrow.
 */
import type { SolveJob, SolveJobKind } from '../api/solveQueue'
import type { EhStudyPayload } from '../api/simulation'
import type { RunEstimate } from '../api/runEstimate'

/** Plain names for each run type (RAW-NAMES-1: never the kind string). */
export const RUN_NAME: Record<SolveJobKind, string> = {
  solve: 'Optimisation',
  campus: 'Campus study',
  gridspine: 'Grid study',
  profile_gen: 'Generating profiles',
}

export interface RunView {
  /** "Optimisation", "Reliability study", … */
  name: string
  /** "Waiting in queue #2", "Solving", "Studying" */
  stage: string
  /** "step 4 of up to 30" (the step in progress); '' when the run has no step count. */
  steps: string
  /** Epoch seconds the run started; null while it waits. */
  startedAt: number | null
  /** The queue job, when the run is one (the estimate is per job). */
  jobId: string | null
  queued: boolean
}

export function runFromJob(job: SolveJob): RunView {
  const kind = job.kind ?? 'solve'
  const queued = job.status === 'queued'
  return {
    name: RUN_NAME[kind as SolveJobKind] ?? 'Run',
    stage: queued
      ? `Waiting in queue${job.position != null ? ` #${job.position}` : ''}`
      : kind === 'solve' ? 'Solving' : 'Running',
    steps: '',
    startedAt: queued ? null : job.started_at,
    jobId: job.id,
    queued,
  }
}

/** The Guided goal study while it runs; null otherwise. */
export function runFromStudy(study: EhStudyPayload | null | undefined): RunView | null {
  if (study?.status !== 'running') return null
  const pipeline = study.report?.pipeline
  const budget = pipeline?.budget_solves ?? study.budget_solves
  const done = pipeline?.solves_consumed
  const steps = budget
    ? done != null ? `step ${Math.min(done + 1, budget)} of up to ${budget}` : `up to ${budget} steps`
    : ''
  return {
    name: 'Reliability study',
    stage: 'Studying',
    steps,
    startedAt: study.started_at ?? null,
    jobId: null,
    queued: false,
  }
}

/** 41 → "0:41", 188 → "3:08". */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

/** A duration in words: "under a minute", "4 min", "15 min" (nearest 5 above 10). */
export function aboutMinutes(seconds: number): string {
  if (seconds < 60) return 'under a minute'
  const min = seconds / 60
  if (min < 10) return `${Math.max(1, Math.round(min))} min`
  return `${Math.round(min / 5) * 5} min`
}

export interface EtaText {
  text: string
  /** Muted: unknown, or no estimate to show. */
  muted: boolean
  /** Tooltip naming the basis; '' when unknown. */
  basis: string
}

/**
 * The ETA words for a run `elapsed` seconds in, or null when the run has no
 * estimate to speak of (a study, or a queued job: it shows its position).
 */
export function etaText(estimate: RunEstimate | null | undefined, elapsed: number): EtaText | null {
  if (!estimate) return null
  if (estimate.seconds == null || !(estimate.seconds > 0)) {
    return { text: 'first runs: no estimate yet', muted: true, basis: '' }
  }
  const n = estimate.samples
  const basis = `Based on your last ${n} run${n === 1 ? '' : 's'} of this project.`
  const left = estimate.seconds - elapsed
  if (left <= 0) {
    return { text: `longer than usual (usually about ${aboutMinutes(estimate.seconds)})`, muted: false, basis }
  }
  const words = left < 60 ? 'under a minute left' : `about ${aboutMinutes(left)} left`
  return { text: words, muted: false, basis }
}

/** The whole line as one string: the line's tooltip and its test oracle. */
export function runStatusText(run: RunView, elapsed: number | null, eta: EtaText | null): string {
  return [run.name, run.stage, run.steps, elapsed != null ? clock(elapsed) : '', eta?.text ?? '']
    .filter(Boolean)
    .join(' · ')
}

// ── How a run ended (slice 2b; audit #25) ──────────────────────────────────

export type RunEnd = 'completed' | 'failed' | 'aborted' | 'interrupted'

/** A run of the project on screen that ended while the app watched it. */
export interface RunOutcome {
  /** The queue job id, or `study:<started_at>` for the Guided goal study. */
  key: string
  name: string
  end: RunEnd
  /** Wall time, when both ends are known. */
  seconds: number | null
  /** The run's own reason, for a failed run; null when none was recorded. */
  error: string | null
  /** The queue job, when the run was one. */
  jobId: string | null
}

const RUN_ENDS: ReadonlySet<string> = new Set(['completed', 'failed', 'aborted', 'interrupted'])

function wallTime(started: number | null | undefined, finished: number | null | undefined): number | null {
  return started != null && finished != null && finished > started ? finished - started : null
}

/** How a queue job ended; null while it is queued or running. */
export function outcomeFromJob(job: SolveJob): RunOutcome | null {
  if (!RUN_ENDS.has(job.status)) return null
  return {
    key: job.id,
    name: RUN_NAME[(job.kind ?? 'solve') as SolveJobKind] ?? 'Run',
    end: job.status as RunEnd,
    seconds: wallTime(job.started_at, job.finished_at),
    error: job.error ?? null,
    jobId: job.id,
  }
}

/** The key that names one Guided goal study (it has no id of its own). */
export function studyKey(study: EhStudyPayload | null | undefined): string | null {
  return study?.started_at != null ? `study:${study.started_at}` : null
}

/** How the Guided goal study ended; null while it runs or when it has not started. */
export function outcomeFromStudy(study: EhStudyPayload | null | undefined): RunOutcome | null {
  const key = studyKey(study)
  // The study record says "done" where a queue job says "completed".
  const end = study?.status === 'done' ? 'completed' : study?.status
  if (!study || !key || !end || !RUN_ENDS.has(end)) return null
  return {
    key,
    name: 'Reliability study',
    end: end as RunEnd,
    seconds: wallTime(study.started_at, study.finished_at),
    error: study.error ?? null,
    jobId: null,
  }
}

/** "Campus study finished · 6 min", "Optimisation failed", "Optimisation stopped". */
export function outcomeText(o: RunOutcome): string {
  switch (o.end) {
    case 'completed': return `${o.name} finished${o.seconds != null ? ` · ${aboutMinutes(o.seconds)}` : ''}`
    case 'failed': return `${o.name} failed`
    case 'interrupted': return `${o.name} was interrupted`
    case 'aborted': return `${o.name} stopped`
  }
}

/** The plain reason shown under "See why" (Guided, in place). */
export function outcomeReason(o: RunOutcome): string {
  if (o.end === 'interrupted') return 'The run stopped when the app restarted. Run it again to get results.'
  return o.error?.trim() || 'No reason was recorded. Run it again, or open the log in Expert.'
}
