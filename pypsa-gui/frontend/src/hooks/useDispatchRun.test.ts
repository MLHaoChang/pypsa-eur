import { describe, expect, it } from 'vitest'
import { dispatchRun } from './useDispatchRun'
import type { SimulationStatus } from '../api/types'

const st = (o: Partial<SimulationStatus>) => ({ status: 'completed', objective: 3.47e6, solve_time: 1,
  dispatch: 'fresh', solved_at: '2026-11-17T15:42:00Z', ...o }) as SimulationStatus

describe('dispatchRun (GK B3 C1/C2)', () => {
  it('is current only with a fresh dispatch the backend calls fresh', () => {
    expect(dispatchRun(st({}), 'fresh')).toEqual({ solved: true, current: true, objective: 3.47e6, at: '2026-11-17T15:42:00Z' })
  })
  it.each(['stale', 'uncompared', null, undefined] as const)('optimisation %s is not current', f => {
    const r = dispatchRun(st({}), f)
    expect(r.solved).toBe(true)
    expect(r.current).toBe(false)
  })
  it('a stale dispatch shape is solved but not current', () => {
    expect(dispatchRun(st({ dispatch: 'stale' }), 'fresh').current).toBe(false)
  })
  it('a reopened project with no stamp is still solved, with no time', () => {
    expect(dispatchRun(st({ solved_at: null }), 'fresh')).toMatchObject({ solved: true, current: true, at: null })
  })
  it('no dispatch, or no objective, is not solved', () => {
    expect(dispatchRun(st({ dispatch: 'none' }), 'fresh').solved).toBe(false)
    expect(dispatchRun(st({ objective: null }), 'fresh').solved).toBe(false)
    expect(dispatchRun(null, 'fresh')).toEqual({ solved: false, current: false, objective: null, at: null })
  })
})
