/**
 * Finish/fail notification rules (run progress spec §3, slice 3; GK B3 N1–N8,
 * UX 2 plan GO 1–6). Pure apart from the `Storage` passed in, so the hook and
 * its tests share one definition of each rule.
 */
import type { SolveJob, SolveJobKind } from '../api/solveQueue'
import { RUN_NAME, aboutMinutes } from './runStatus'
import { PROJECT_LOCKED, lockFromErrorData, lockRefusalCode } from './lockState'

/** The outcomes that notify. An aborted run notifies nobody: they did it. */
export type NoticeEnd = 'completed' | 'failed' | 'interrupted'

const NOTICE_ENDS: ReadonlySet<string> = new Set<NoticeEnd>(['completed', 'failed', 'interrupted'])

/** How long a claim or an away count looks back (spec §3: "at most 24 h"). */
export const AWAY_WINDOW_S = 24 * 60 * 60

const CLAIM_PREFIX = 'run-notified:'
const LAST_SEEN_PREFIX = 'run-last-seen:'

export function noticeEnd(job: SolveJob): NoticeEnd | null {
  return NOTICE_ENDS.has(job.status) ? (job.status as NoticeEnd) : null
}

export function runName(job: SolveJob): string {
  return RUN_NAME[(job.kind ?? 'solve') as SolveJobKind] ?? 'Run'
}

/**
 * The words of a toast and of the hidden-tab notification: the run, the
 * outcome, the project and, for a finished run, how long it took. Never a run
 * id, an engine name or the run's own error text (UX 6, GK N6).
 */
export function noticeText(job: SolveJob): string | null {
  const end = noticeEnd(job)
  const project = job.project_id
  if (!end || !project) return null
  if (end === 'interrupted') return `Run stopped when the app restarted: ${project}`
  if (end === 'failed') return `${runName(job)} failed: ${project}`
  const s = job.started_at != null && job.finished_at != null && job.finished_at > job.started_at
    ? job.finished_at - job.started_at : null
  return `${runName(job)} finished: ${project}${s != null ? ` · ${aboutMinutes(s)}` : ''}`
}

/** Whether a finished job of the caller's own is one the user should hear about. */
export function isMyNotice(job: SolveJob): boolean {
  return job.started_by_me === true && noticeEnd(job) !== null && job.project_id != null
}

/**
 * Claim a job so only one open tab notifies about it (spec §3). Set if absent:
 * true means this tab owns the notice. Storage that throws (private mode,
 * blocked site data) notifies anyway: a possible duplicate, never a missed one.
 */
export function claim(storage: Storage | null, jobId: string, now: number): boolean {
  if (!storage) return true
  try {
    const key = CLAIM_PREFIX + jobId
    if (storage.getItem(key) !== null) return false
    storage.setItem(key, String(now))
    return true
  } catch {
    return true
  }
}

export function isClaimed(storage: Storage | null, jobId: string): boolean {
  try { return storage?.getItem(CLAIM_PREFIX + jobId) != null } catch { return false }
}

/** Drop claims older than the away window, so storage does not grow forever. */
export function pruneClaims(storage: Storage | null, now: number): void {
  if (!storage) return
  try {
    const stale: string[] = []
    for (let i = 0; i < storage.length; i++) {
      const key = storage.key(i)
      if (!key?.startsWith(CLAIM_PREFIX)) continue
      const at = Number(storage.getItem(key))
      if (!Number.isFinite(at) || now - at > AWAY_WINDOW_S) stale.push(key)
    }
    stale.forEach(k => storage.removeItem(k))
  } catch { /* storage unavailable: nothing to prune */ }
}

/** When this user's tab last saw the queue, per user (GK N4); null when never. */
export function lastSeen(storage: Storage | null, userKey: string): number | null {
  try {
    const v = storage?.getItem(LAST_SEEN_PREFIX + userKey)
    const n = v == null ? NaN : Number(v)
    return Number.isFinite(n) ? n : null
  } catch {
    return null
  }
}

export function markSeen(storage: Storage | null, userKey: string, now: number): void {
  try { storage?.setItem(LAST_SEEN_PREFIX + userKey, String(now)) } catch { /* noop */ }
}

/**
 * My runs that ended while no tab of mine watched: after `since` (the last
 * time a tab saw the queue), within the away window, and not already claimed
 * by a tab that toasted them. With no `since` (first visit) nothing counts,
 * so a first sign-in does not report a day of old runs as news.
 */
export function awayJobs(
  jobs: SolveJob[], since: number | null, now: number, storage: Storage | null,
): SolveJob[] {
  if (since == null) return []
  return jobs.filter(j =>
    isMyNotice(j)
    && j.finished_at != null
    && j.finished_at > since
    && now - j.finished_at <= AWAY_WINDOW_S
    && !isClaimed(storage, j.id),
  )
}

/** "1 run finished while you were away", "2 runs …". */
export function awayText(count: number): string {
  return `${count} run${count === 1 ? '' : 's'} finished while you were away`
}

/**
 * A browser notification only when the tab is hidden, the browser granted
 * permission and the user has not switched it off (spec §3).
 */
export function shouldNotifyBrowser(hidden: boolean, permission: string | null, enabled: boolean): boolean {
  return hidden && permission === 'granted' && enabled
}

/** The browser's notification permission, or null where there is no Notification API. */
export function notifyPermission(): NotificationPermission | null {
  return typeof Notification === 'undefined' ? null : Notification.permission
}

/**
 * A refused "Run again", in plain words (GK N3). Never silent, never read as
 * success, never the route's raw text.
 */
export function runAgainError(err: unknown, project: string): string {
  const res = (err as { response?: { status?: number; data?: unknown } })?.response
  if (res?.status === 409 && lockRefusalCode(res.data) === PROJECT_LOCKED) {
    const who = lockFromErrorData(res.data)?.holder_email ?? 'another user'
    return `Can't run it again: ${who} is editing ${project}.`
  }
  if (res?.status === 409) return "Can't run it again: the run is still going on."
  if (res?.status === 404) return `Can't run it again: ${project} has no saved copy to run. Save it, then run it again.`
  if (res?.status === 403) return `Can't run it again: you can't run ${project}.`
  return "Can't run it again: the app did not answer. Try again from the Solve queue."
}
