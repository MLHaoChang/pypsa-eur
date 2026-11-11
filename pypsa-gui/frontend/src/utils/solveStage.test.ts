import { describe, expect, it } from 'vitest'
import { solveStageIndex, solveStageLabel } from './solveStage'

const P = (m: string) => `[PHASE] ${m}`

describe('solveStage', () => {
  it('starts at Checking inputs with no lines', () => {
    expect(solveStageIndex([])).toBe(0)
    expect(solveStageLabel([])).toBe('Checking inputs, step 1 of 4')
  })
  it('moves through the four stages in order', () => {
    const l = [P('Running pre-flight validation...')]
    expect(solveStageIndex(l)).toBe(0)
    l.push(P('Validation passed (0 warnings).'))
    expect(solveStageIndex(l)).toBe(1)
    l.push(P('Optimising via highs (LOPF)...'))
    expect(solveStageIndex(l)).toBe(2)
    l.push(P('Solve complete (status=ok, condition=optimal, 3.20 s)'))
    expect(solveStageIndex(l)).toBe(3)
    expect(solveStageLabel(l)).toBe('Saving results, step 4 of 4')
  })
  it('never goes back and ignores non-PHASE lines', () => {
    expect(solveStageIndex([P('Optimising via highs (LOPF)...'), P('Validation passed (0 warnings).'), 'Optimising via x'])).toBe(2)
    expect(solveStageIndex(['Solve complete'])).toBe(0)
  })
  it('a failed validation stays at Checking inputs', () => {
    expect(solveStageIndex([P('Validation failed: 2 error(s). Aborting.')])).toBe(0)
  })
})
