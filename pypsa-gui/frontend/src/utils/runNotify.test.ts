import { beforeEach, describe, expect, it } from 'vitest'
import type { SolveJob } from '../api/solveQueue'
import {
  AWAY_WINDOW_S, awayJobs, awayText, claim, isMyNotice, lastSeen, markSeen, noticeText, osTitle,
  pruneClaims, runAgainError, shouldNotifyBrowser,
} from './runNotify'

const job = (over: Partial<SolveJob> = {}): SolveJob => ({
  id: 'j1', project_id: 'Campus A', project_key: 'o:p', kind: 'campus', status: 'completed', position: null,
  objective: null, solve_time: null, condition: null, error: 'Traceback: HiGHS status 7',
  enqueued_at: 100, started_at: 1000, finished_at: 1360, can_dismiss: true, started_by_me: true, ...over,
} as SolveJob)

describe('noticeText', () => {
  it('names run, outcome, project and duration, with colons', () => {
    expect(noticeText(job())).toBe('Campus study finished: Campus A · 6 min')
    expect(noticeText(job({ status: 'failed' }))).toBe('Campus study failed: Campus A')
    expect(noticeText(job({ status: 'interrupted' }))).toBe('Run stopped when the app restarted: Campus A')
  })
  it('never carries the error text, the run id or the kind string', () => {
    for (const status of ['completed', 'failed', 'interrupted'] as const) {
      const t = noticeText(job({ status, id: 'abc-123' }))!
      expect(t).not.toMatch(/HiGHS|Traceback|abc-123|campus\b/)
    }
  })
  it('an aborted run, a running run or a redacted row has no notice', () => {
    expect(noticeText(job({ status: 'aborted' }))).toBeNull()
    expect(noticeText(job({ status: 'running' }))).toBeNull()
    expect(noticeText(job({ project_id: null }))).toBeNull()
  })
})

describe('osTitle', () => {
  it('names the run, how it ended and the project; no duration, no error', () => {
    expect(osTitle(job())).toBe('Campus study finished: Campus A')
    expect(osTitle(job({ status: 'failed' }))).toBe('Campus study failed: Campus A')
    expect(osTitle(job({ status: 'interrupted' }))).toBeNull()
    expect(osTitle(job({ status: 'aborted' }))).toBeNull()
    expect(osTitle(job({ project_id: null }))).toBeNull()
  })
})

describe('isMyNotice', () => {
  it('only my own runs notify', () => {
    expect(isMyNotice(job())).toBe(true)
    expect(isMyNotice(job({ started_by_me: false }))).toBe(false)
    expect(isMyNotice(job({ started_by_me: undefined }))).toBe(false)
    expect(isMyNotice(job({ status: 'aborted' }))).toBe(false)
  })
})

describe('claim', () => {
  beforeEach(() => localStorage.clear())
  it('the first tab claims a job, a second does not', () => {
    expect(claim(localStorage, 'j1', 5)).toBe(true)
    expect(claim(localStorage, 'j1', 6)).toBe(false)
    expect(claim(localStorage, 'j2', 6)).toBe(true)
  })
  it('notifies anyway when storage throws', () => {
    const broken = { getItem: () => { throw new Error('blocked') }, setItem: () => { throw new Error('blocked') } } as unknown as Storage
    expect(claim(broken, 'j1', 5)).toBe(true)
    expect(claim(null, 'j1', 5)).toBe(true)
  })
  it('prunes claims older than the away window only', () => {
    claim(localStorage, 'old', 0)
    claim(localStorage, 'new', AWAY_WINDOW_S)
    pruneClaims(localStorage, AWAY_WINDOW_S + 10)
    expect(localStorage.getItem('run-notified:old')).toBeNull()
    expect(localStorage.getItem('run-notified:new')).not.toBeNull()
  })
})

describe('last seen and the away summary', () => {
  beforeEach(() => localStorage.clear())
  it('last seen is kept per user', () => {
    markSeen(localStorage, 'u1', 50)
    expect(lastSeen(localStorage, 'u1')).toBe(50)
    expect(lastSeen(localStorage, 'u2')).toBeNull()
  })
  it('counts my runs that ended after last seen, inside 24 h, not claimed', () => {
    const now = 2000
    const jobs = [
      job({ id: 'a', finished_at: 1500 }),
      job({ id: 'b', status: 'failed', finished_at: 1600 }),
      job({ id: 'before', finished_at: 900 }),
      job({ id: 'theirs', finished_at: 1500, started_by_me: false }),
      job({ id: 'aborted', status: 'aborted', finished_at: 1500 }),
      job({ id: 'claimed', finished_at: 1500 }),
    ]
    claim(localStorage, 'claimed', 1500)
    expect(awayJobs(jobs, 1000, now, localStorage).map(j => j.id)).toEqual(['a', 'b'])
  })
  it('caps the look-back at 24 h, and a first visit counts nothing', () => {
    const now = AWAY_WINDOW_S + 5000
    const jobs = [job({ id: 'old', finished_at: 4000 }), job({ id: 'edge', finished_at: 5000 })]
    expect(awayJobs(jobs, 0, now, localStorage).map(j => j.id)).toEqual(['edge'])
    expect(awayJobs(jobs, null, now, localStorage)).toEqual([])
  })
  it('says how many', () => {
    expect(awayText(1)).toBe('1 run finished while you were away')
    expect(awayText(2)).toBe('2 runs finished while you were away')
  })
})

describe('shouldNotifyBrowser', () => {
  it('needs a hidden tab, granted permission and the setting on', () => {
    expect(shouldNotifyBrowser(true, 'granted', true)).toBe(true)
    expect(shouldNotifyBrowser(false, 'granted', true)).toBe(false)
    expect(shouldNotifyBrowser(true, 'default', true)).toBe(false)
    expect(shouldNotifyBrowser(true, 'denied', true)).toBe(false)
    expect(shouldNotifyBrowser(true, 'granted', false)).toBe(false)
    expect(shouldNotifyBrowser(true, null, true)).toBe(false)
  })
})

describe('runAgainError', () => {
  const err = (status: number, detail: unknown) => ({ response: { status, data: { detail } } })
  it('names the lock holder for project_locked', () => {
    expect(runAgainError(err(409, { error_kind: 'project_locked', message: 'x', lock: { holder_email: 'ana@example.com' } }), 'Campus A'))
      .toBe("Can't run it again: ana@example.com is editing Campus A.")
  })
  it('says why for each refusal, never the raw route text', () => {
    expect(runAgainError(err(409, 'Job 1 is running, not finished.'), 'P')).toBe("Can't run it again: the run is still going on.")
    expect(runAgainError(err(404, 'No solve job with id 1.'), 'P')).toMatch(/^Can't run it again: P has no saved copy/)
    expect(runAgainError(err(403, 'nope'), 'P')).toBe("Can't run it again: you can't run P.")
    // A viewer (#1622): the route's write gate refuses with project_view_only.
    expect(runAgainError(err(403, { error_kind: 'project_view_only', message: 'x' }), 'P'))
      .toBe("Can't run it again: you can view P but not run it.")
    expect(runAgainError(new Error('Network Error'), 'P')).toMatch(/^Can't run it again: the app did not answer/)
  })
})
