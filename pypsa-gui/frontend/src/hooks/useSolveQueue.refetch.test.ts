// 2026-08-14 review: `refetchInterval` returned `false` whenever there was no
// data — including the ERROR state. With `retry: 1` and the app-wide
// `refetchOnWindowFocus: false`, a backend blip during the first fetch left
// the queue permanently dead: no interval, no focus refetch, no retry
// affordance, until the panel was closed and reopened. An error state must
// keep probing (slowly), and the idle/active behaviour must stay as it was.
import { describe, expect, it } from 'vitest'
import { queueRefetchInterval, QUEUE_ERROR_RETRY_MS } from './useSolveQueue'
import type { SolveJob } from '../api/solveQueue'

function job(status: SolveJob['status']): SolveJob {
  return {
    id: '1', project_id: 'p', project_key: null, status,
    position: null, objective: null, solve_time: null, condition: null, error: null,
    enqueued_at: 0, started_at: null, finished_at: null,
  }
}

function q(status: 'pending' | 'error' | 'success', jobs?: SolveJob[]) {
  return { state: { status, data: jobs ? { jobs, running: [], paused: false } : undefined } }
}

describe('queueRefetchInterval', () => {
  it('keeps probing after an error instead of going permanently dead', () => {
    expect(queueRefetchInterval(q('error'))).toBe(QUEUE_ERROR_RETRY_MS)
  })

  it('polls fast while a job is active', () => {
    expect(queueRefetchInterval(q('success', [job('running')]))).toBe(1500)
    expect(queueRefetchInterval(q('success', [job('queued')]))).toBe(1500)
  })

  it('stops polling when the queue is idle', () => {
    expect(queueRefetchInterval(q('success', [job('completed')]))).toBe(false)
    expect(queueRefetchInterval(q('success', []))).toBe(false)
  })
})
