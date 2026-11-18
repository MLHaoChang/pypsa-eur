import { useEffect, useRef } from 'react'
import { isActive, type SolveJob } from '../api/solveQueue'
import { useUIStore } from '../store/uiStore'
import { useSolveQueue } from './useSolveQueue'
import { statusMap, terminalJobs } from './useJobTerminalInvalidation'
import {
  awayJobs, claim, isMyNotice, lastSeen, markSeen, noticeText, notifyPermission, pruneClaims,
  shouldNotifyBrowser,
} from '../utils/runNotify'
import { awayNotice, noticeFromJob, useRunNotices, type RunNotice } from '../components/runStatus/runNotices'
import { openNoticeResults, openNoticeWhy } from '../components/runStatus/noticeActions'

function safeStorage(): Storage | null {
  try { return window.localStorage } catch { return null }
}

/**
 * One notice per run of mine that ends, in the tab that claims it first (run
 * progress spec §3, slice 3).
 *
 * The first queue listing after mount only feeds the "while you were away"
 * summary (GK B3 N5): a job already ended before this tab loaded never gets a
 * toast of its own, and the summary claims every job it counts, so a later
 * poll cannot toast it again (N4). Last-seen is written only after that.
 * Later listings notify each of my jobs that just ended, through the same
 * `terminalJobs` rule that refreshes the caches. When the tab is hidden, the
 * browser notifies too, if the user allowed it.
 *
 * `userKey` keeps last-seen per account, so a second account in the same
 * browser neither suppresses nor inherits the summary.
 */
export function useRunNotifications(userKey: string): void {
  const { data } = useSolveQueue()
  const jobs = data?.jobs
  const prevRef = useRef<Map<string, string> | null>(null)
  const userRef = useRef(userKey)
  userRef.current = userKey

  useEffect(() => {
    if (jobs === undefined) return
    const now = Date.now() / 1000
    const storage = safeStorage()
    const user = userRef.current
    const { push, dismissWhere } = useRunNotices.getState()
    const prev = prevRef.current
    prevRef.current = statusMap(jobs)

    if (prev === null) {
      pruneClaims(storage, now)
      const away = awayJobs(jobs, lastSeen(storage, user), now, storage)
      away.forEach(j => claim(storage, j.id, now))
      if (away.length > 0) push(awayNotice(away))
      markSeen(storage, user, now)
      return
    }

    for (const job of terminalJobs(prev, jobs)) {
      if (!isMyNotice(job)) continue
      if (!claim(storage, job.id, now)) continue
      const notice = noticeFromJob(job)
      if (!notice) continue
      push(notice)
      notifyBrowser(job, notice)
    }

    // A failed or interrupted notice is over once its project runs again (UX 3).
    const running = new Set(jobs.filter(isActive).map(j => j.project_id))
    dismissWhere(n => (n.kind === 'failed' || n.kind === 'interrupted') && running.has(n.project))
    markSeen(storage, user, now)
  }, [jobs])
}

/**
 * The hidden-tab notification: the toast's own words, nothing more (GK N6).
 * An interrupted run has none (spec §3: the restart is the app's, not news).
 */
function notifyBrowser(job: SolveJob, notice: RunNotice): void {
  if (notice.kind !== 'completed' && notice.kind !== 'failed') return
  const hidden = typeof document !== 'undefined' && document.hidden
  if (!shouldNotifyBrowser(hidden, notifyPermission(), useUIStore.getState().browserNotify)) return
  const title = noticeText(job)
  if (!title) return
  try {
    const n = new Notification(title, { tag: `run-${job.id}` })
    n.onclick = () => {
      window.focus()
      if (notice.kind === 'completed') openNoticeResults(notice)
      else openNoticeWhy()
      n.close()
    }
  } catch { /* the toast is already there */ }
}
