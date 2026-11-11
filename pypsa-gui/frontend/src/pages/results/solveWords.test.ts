import { describe, expect, it } from 'vitest'
import { solveWords } from './solveWords'

describe('solveWords', () => {
  it.each(['optimal', 'ok', 'lopf+ac_pf_ok', 'ac_pf_ok'])('reads %s as solved', c => {
    expect(solveWords(c)).toBe('solved')
  })

  it('reads the optimisation\'s own condition, before ";"', () => {
    expect(solveWords('optimal; ac_pf_failed: did not converge')).toBe('solved')
    expect(solveWords('infeasible; ac_pf_failed: skipped')).toBe('has no feasible answer')
  })

  it('keeps the words of a solve that did not succeed', () => {
    expect(solveWords('infeasible')).toBe('has no feasible answer')
    expect(solveWords('suboptimal')).toBe('did not converge')
    expect(solveWords('time_limit')).toBe('hit the time limit')
  })

  it('says "did not solve" for an unknown or missing condition', () => {
    expect(solveWords('warning')).toBe('did not solve')
    expect(solveWords('')).toBe('did not solve')
    expect(solveWords(null)).toBe('did not solve')
  })
})
