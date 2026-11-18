import { create } from 'zustand'
import type { SolveJob } from '../../api/solveQueue'
import { awayText, noticeEnd, noticeText, runName, type NoticeEnd } from '../../utils/runNotify'
import { outcomeFromJob, outcomeReason } from '../../utils/runStatus'

/**
 * The run notices on screen (run progress slice 3). One per run of mine that
 * ended while a tab of mine watched, plus at most one "while you were away"
 * summary. A finished run's notice goes by itself; a failed or interrupted
 * one stays until closed, acted on, or a new run of its project starts
 * (UX 2 GO 3).
 */
export interface RunNotice {
  /** The job id, or `away` for the summary. */
  id: string
  kind: NoticeEnd | 'away'
  text: string
  project: string | null
  jobId: string | null
  /** "Campus study": for "Run again: Campus study". */
  runName: string
  /** The plain reason behind "See why"; only for failed runs. */
  reason: string | null
  /** The summary's lines, one per run. */
  lines: string[]
}

/** How long a finished run's notice stays (UX 7: it goes by itself). */
export const FINISHED_NOTICE_MS = 8000

interface NoticeState {
  notices: RunNotice[]
  push: (n: RunNotice) => void
  dismiss: (id: string) => void
  dismissWhere: (pred: (n: RunNotice) => boolean) => void
}

const timers = new Map<string, ReturnType<typeof setTimeout>>()

function clearTimer(id: string): void {
  const t = timers.get(id)
  if (t) clearTimeout(t)
  timers.delete(id)
}

export const useRunNotices = create<NoticeState>((set, get) => ({
  notices: [],
  push: (n) => {
    clearTimer(n.id)
    set(s => ({ notices: [n, ...s.notices.filter(o => o.id !== n.id)] }))
    if (n.kind === 'completed') {
      timers.set(n.id, setTimeout(() => get().dismiss(n.id), FINISHED_NOTICE_MS))
    }
  },
  dismiss: (id) => {
    clearTimer(id)
    set(s => ({ notices: s.notices.filter(n => n.id !== id) }))
  },
  dismissWhere: (pred) => {
    const gone = get().notices.filter(pred)
    if (gone.length === 0) return
    gone.forEach(n => clearTimer(n.id))
    set(s => ({ notices: s.notices.filter(n => !pred(n)) }))
  },
}))

/** For tests: forget every notice and timer. */
export function resetRunNotices(): void {
  timers.forEach(t => clearTimeout(t))
  timers.clear()
  useRunNotices.setState({ notices: [] })
}

export function noticeFromJob(job: SolveJob): RunNotice | null {
  const kind = noticeEnd(job)
  const text = noticeText(job)
  if (!kind || !text) return null
  const outcome = outcomeFromJob(job)
  return {
    id: job.id,
    kind,
    text,
    project: job.project_id,
    jobId: job.id,
    runName: runName(job),
    reason: kind === 'failed' && outcome ? outcomeReason(outcome) : null,
    lines: [],
  }
}

export function awayNotice(jobs: SolveJob[]): RunNotice {
  return {
    id: 'away',
    kind: 'away',
    text: awayText(jobs.length),
    project: null,
    jobId: null,
    runName: '',
    reason: null,
    lines: jobs.map(j => noticeText(j)).filter((t): t is string => t != null),
  }
}
