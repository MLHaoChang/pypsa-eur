// Run progress slice 3 (spec §3; GK B3 N4, N5, N6, N7): which queue changes
// become a notice, in which tab, and when the browser is told too. The queue
// is a controlled `useSolveQueue` mock driven across renders, as in
// useJobTerminalInvalidation.test.tsx.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import type { SolveJob } from '../api/solveQueue'
import { useRunNotifications } from './useRunNotifications'
import { useSolveQueue } from './useSolveQueue'
import { resetRunNotices, useRunNotices } from '../components/runStatus/runNotices'
import { useUIStore } from '../store/uiStore'

vi.mock('./useSolveQueue', () => ({ useSolveQueue: vi.fn() }))

const NOW = 100_000

function job(id: string, status: SolveJob['status'], over: Partial<SolveJob> = {}): SolveJob {
  return {
    id, project_id: 'Campus A', project_key: 'o:p', kind: 'campus', status,
    position: null, objective: null, solve_time: null, condition: null, error: 'HiGHS status 7',
    enqueued_at: NOW - 600, started_at: NOW - 400, finished_at: status === 'running' ? null : NOW - 40,
    can_dismiss: false, started_by_me: true, ...over,
  }
}

function serve(jobs: SolveJob[]) {
  vi.mocked(useSolveQueue).mockReturnValue({ data: { jobs: [...jobs] } } as unknown as ReturnType<typeof useSolveQueue>)
}

const texts = () => useRunNotices.getState().notices.map(n => n.text)

let notified: { title: string }[]
let permission: NotificationPermission

class FakeNotification {
  static get permission() { return permission }
  onclick: (() => void) | null = null
  constructor(title: string) { notified.push({ title }) }
  close() {}
}

function setHidden(hidden: boolean) {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
}

beforeEach(() => {
  localStorage.clear()
  resetRunNotices()
  notified = []
  permission = 'granted'
  vi.stubGlobal('Notification', FakeNotification)
  vi.spyOn(Date, 'now').mockReturnValue(NOW * 1000)
  useUIStore.setState({ browserNotify: true })
  setHidden(false)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('per-run notices', () => {
  it('a run of mine that ends gives one notice per outcome; an aborted run gives none', () => {
    serve([job('a', 'running'), job('b', 'running'), job('c', 'running'), job('d', 'running')])
    const { rerender } = renderHook(() => useRunNotifications('u1'))
    serve([job('a', 'completed'), job('b', 'failed'), job('c', 'interrupted'), job('d', 'aborted')])
    rerender()
    expect(texts()).toEqual([
      'Run stopped when the app restarted: Campus A',
      'Campus study failed: Campus A',
      'Campus study finished: Campus A · 6 min',
    ])
  })

  it("a colleague's run stays silent", () => {
    serve([job('a', 'running', { started_by_me: false })])
    const { rerender } = renderHook(() => useRunNotifications('u1'))
    serve([job('a', 'completed', { started_by_me: false })])
    rerender()
    expect(texts()).toEqual([])
  })

  it('a run already ended when the tab loaded gets no notice of its own (N5)', () => {
    serve([job('a', 'completed', { finished_at: NOW - 10 })])
    const { rerender } = renderHook(() => useRunNotifications('u1'))
    serve([job('a', 'completed', { finished_at: NOW - 10 })])
    rerender()
    expect(texts()).toEqual([])
  })

  it('two tabs on one browser notify once', () => {
    // The notice store is shared in this test, so count the hidden-tab
    // notifications: each tab that claimed the job would send one.
    setHidden(true)
    serve([job('a', 'running')])
    const tab1 = renderHook(() => useRunNotifications('u1'))
    const tab2 = renderHook(() => useRunNotifications('u1'))
    serve([job('a', 'completed')])
    tab1.rerender()
    tab2.rerender()
    expect(texts()).toHaveLength(1)
    expect(notified).toHaveLength(1)
  })

  it('still notifies when storage throws', () => {
    serve([job('a', 'running')])
    const { rerender } = renderHook(() => useRunNotifications('u1'))
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
    serve([job('a', 'failed')])
    rerender()
    expect(texts()).toEqual(['Campus study failed: Campus A'])
  })

  it('a failed notice goes once its project runs again', () => {
    serve([job('a', 'running')])
    const { rerender } = renderHook(() => useRunNotifications('u1'))
    serve([job('a', 'failed')])
    rerender()
    serve([job('a', 'failed'), job('b', 'queued')])
    rerender()
    expect(texts()).toEqual([])
  })
})

describe('browser notification', () => {
  function endRun(status: SolveJob['status']) {
    serve([job('a', 'running')])
    const { rerender } = renderHook(() => useRunNotifications('u1'))
    serve([job('a', status)])
    rerender()
  }

  it('only when the tab is hidden, permission is granted and the setting is on', () => {
    setHidden(true)
    endRun('completed')
    expect(notified).toEqual([{ title: 'Campus study finished: Campus A · 6 min' }])
  })

  it.each([
    ['visible tab', () => setHidden(false)],
    ['permission not granted', () => { setHidden(true); permission = 'default' }],
    ['setting off', () => { setHidden(true); useUIStore.setState({ browserNotify: false }) }],
  ])('none with a %s', (_name, arrange) => {
    arrange()
    endRun('completed')
    expect(notified).toEqual([])
    expect(texts()).toHaveLength(1)
  })

  it('carries the toast words only, never the error text (N6)', () => {
    setHidden(true)
    endRun('failed')
    expect(notified).toEqual([{ title: 'Campus study failed: Campus A' }])
  })

  it('none for an interrupted run', () => {
    setHidden(true)
    endRun('interrupted')
    expect(notified).toEqual([])
  })

  it('never asks for permission by itself', () => {
    const ask = vi.fn()
    ;(FakeNotification as unknown as { requestPermission: () => void }).requestPermission = ask
    permission = 'default'
    endRun('completed')
    expect(ask).not.toHaveBeenCalled()
  })
})

describe('missed while away (N4)', () => {
  it('the first listing gives one summary of my runs since last seen; the next gives nothing', () => {
    localStorage.setItem('run-last-seen:u1', String(NOW - 3600))
    const jobs = [
      job('a', 'completed', { finished_at: NOW - 100 }),
      job('b', 'failed', { finished_at: NOW - 50 }),
      job('old', 'completed', { finished_at: NOW - 7200 }),
    ]
    serve(jobs)
    const { rerender } = renderHook(() => useRunNotifications('u1'))
    expect(texts()).toEqual(['2 runs finished while you were away'])
    expect(Number(localStorage.getItem('run-last-seen:u1'))).toBe(NOW)

    useRunNotices.getState().dismiss('away')
    serve(jobs)
    rerender()
    expect(texts()).toEqual([])

    // A second mount (a reload) finds them claimed and last-seen moved on.
    renderHook(() => useRunNotifications('u1'))
    expect(texts()).toEqual([])
  })

  it('a run the away summary counted is not toasted again by a tab already open (N4)', () => {
    setHidden(true)
    // Tab 1 has been open on an idle queue: its baseline lacks job a.
    serve([])
    const tab1 = renderHook(() => useRunNotifications('u1'))
    localStorage.setItem('run-last-seen:u1', String(NOW - 3600))
    // Tab 2 loads and counts a in its summary.
    serve([job('a', 'completed', { finished_at: NOW - 100 })])
    renderHook(() => useRunNotifications('u1'))
    expect(texts()).toEqual(['1 run finished while you were away'])
    // Tab 1's next listing shows a for the first time, already ended.
    tab1.rerender()
    expect(texts()).toEqual(['1 run finished while you were away'])
    expect(notified).toEqual([])
  })

  it('last seen is per account', () => {
    localStorage.setItem('run-last-seen:u1', String(NOW - 3600))
    serve([job('a', 'completed', { finished_at: NOW - 100 })])
    renderHook(() => useRunNotifications('u2'))
    expect(texts()).toEqual([])
    expect(localStorage.getItem('run-last-seen:u1')).toBe(String(NOW - 3600))
  })
})
