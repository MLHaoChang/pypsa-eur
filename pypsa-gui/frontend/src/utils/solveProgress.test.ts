import { describe, expect, it } from 'vitest'
import { solveProgressLabel } from './solveProgress'

describe('solveProgressLabel', () => {
  it('says Solving… for an LP run with no stage lines', () => {
    expect(solveProgressLabel([])).toBe('Solving…')
    expect(solveProgressLabel(['[PHASE] Optimising via highs (LOPF)...'])).toBe('Solving…')
  })
  it('uses the latest gridspine stage line, in plain words', () => {
    expect(solveProgressLabel(['gridspine ingest 0/6', 'gridspine loadflow 3/6']))
      .toBe('Checking the grid · step 4 of 6')
  })
  it('reads a timestamped campus line and caps the step at the total', () => {
    expect(solveProgressLabel(['[12:00:01] INFO campus size 4/9'])).toBe('Sizing equipment · step 5 of 9')
    expect(solveProgressLabel(['campus done 9/9'])).toBe('Finishing · step 9 of 9')
  })
  it('never shows an engine id for an unknown stage', () => {
    expect(solveProgressLabel(['gridspine zz_new 1/3'])).toBe('Working · step 2 of 3')
  })
})
