// Solver Settings never sends the commercial config (IC P3 WP3.5, plan C7):
// it has no commercial controls, and a stale copy in its PUT would replace
// what the commercial editors (and the value-flow route) saved meanwhile.
import { describe, expect, it } from 'vitest'
import type { SolverConfig } from '../api/types'
import { solverSettingsSaveBody } from './SolverSettings'

const COMMERCIAL = { poc_link: 'import', value_flows: { template: 'btm_ppa' } }
const BASE = { solver_name: 'highs', voll: 3000, commercial: COMMERCIAL } as unknown as SolverConfig

describe('solverSettingsSaveBody', () => {
  it('sends only the changed fields, never commercial', () => {
    const next = { ...BASE, voll: 4000, commercial: { poc_link: 'stale' } } as unknown as SolverConfig
    expect(solverSettingsSaveBody(next, BASE)).toEqual({ voll: 4000 })
  })

  it('is a no-op when only commercial differs', () => {
    const next = { ...BASE, commercial: null } as unknown as SolverConfig
    expect(solverSettingsSaveBody(next, BASE)).toBeNull()
  })

  it('strips commercial from the no-baseline full payload too', () => {
    const body = solverSettingsSaveBody(BASE, null) as Record<string, unknown>
    expect(body).toEqual({ solver_name: 'highs', voll: 3000 })
    expect('commercial' in body).toBe(false)
  })
})
