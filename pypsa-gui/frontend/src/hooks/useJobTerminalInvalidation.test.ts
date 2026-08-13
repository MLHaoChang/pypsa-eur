// R8/R9 — the `>0 → 0` resync is gone; a job going terminal invalidates only
// ITS OWN project's caches.
//
// The deleted effect fired a full `projectsApi.load(currentProject)` — which is
// `reset_network()` + `import_from_netcdf` — on any transition of the GLOBAL
// active-job count to zero, with no save in front of it. The project reloaded
// need never have been queued: queue A, switch to B, edit B, A finishes, B
// reverts to its last saved state. The count also included other organisations'
// redacted rows, so another tenant's batch draining reloaded this user's editor.
import { describe, expect, it } from 'vitest'
import { statusMap, terminalTransitions } from './useJobTerminalInvalidation'
import type { SolveJob } from '../api/solveQueue'

// `id` widened from a per-process integer to a UUID string in increment 3
// (Task 12, R23). `statusMap` stringifies the id precisely so THAT function
// needed no change when it widened — but this helper builds `SolveJob`
// objects directly, so its own `id` parameter has to track the real type or a
// literal here would fail `tsc -b` with TS2322. The values themselves are
// arbitrary distinct tokens for equality comparisons, not real UUIDs.
function job(id: string, project_id: string | null, status: SolveJob['status']): SolveJob {
  return {
    id, project_id, project_key: null, status,
    position: null, objective: null, solve_time: null, condition: null, error: null,
    enqueued_at: 0, started_at: null, finished_at: null,
  }
}

describe('terminalTransitions', () => {
  it('reports the project of a job that just went terminal', () => {
    const prev = statusMap([job('1', 'alpha', 'running')])
    expect(terminalTransitions(prev, [job('1', 'alpha', 'completed')])).toEqual(['alpha'])
  })

  it('reports nothing when a job is already terminal and has not moved', () => {
    const prev = statusMap([job('1', 'alpha', 'completed')])
    expect(terminalTransitions(prev, [job('1', 'alpha', 'completed')])).toEqual([])
  })

  it('reports nothing for a job seen for the first time', () => {
    // A first poll must not invalidate the whole history of finished jobs.
    expect(terminalTransitions(new Map(), [job('1', 'alpha', 'completed')])).toEqual([])
  })

  it('touches only the finishing job\'s project, not every project in the list', () => {
    const prev = statusMap([job('1', 'alpha', 'running'), job('2', 'beta', 'queued')])
    const next = [job('1', 'alpha', 'failed'), job('2', 'beta', 'queued')]
    expect(terminalTransitions(prev, next)).toEqual(['alpha'])
  })

  it('treats every terminal status alike', () => {
    // `interrupted` became a member of `SolveJobStatus` in increment 3 (R27);
    // it's included here now that it exists as a literal. `isTerminal` is the
    // single definition of the set, so this loop needed no other change.
    for (const s of ['completed', 'failed', 'aborted', 'interrupted'] as const) {
      const prev = statusMap([job('1', 'alpha', 'running')])
      expect(terminalTransitions(prev, [job('1', 'alpha', s)])).toEqual(['alpha'])
    }
  })

  it('skips a redacted row, whose project_id is null', () => {
    const prev = statusMap([job('1', null, 'running')])
    expect(terminalTransitions(prev, [job('1', null, 'completed')])).toEqual([])
  })

  it('de-duplicates two jobs of the same project finishing together', () => {
    const prev = statusMap([job('1', 'alpha', 'running'), job('2', 'alpha', 'running')])
    const next = [job('1', 'alpha', 'completed'), job('2', 'alpha', 'aborted')]
    expect(terminalTransitions(prev, next)).toEqual(['alpha'])
  })
})
