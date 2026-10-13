// P24-FE re-gate R8 and notes: every rule of the Improve card's glossary has a
// case, including the review effects the three templates do not reach.
import { describe, expect, it } from 'vitest'
import { evidenceValue, plainWords } from './plainWords'

describe('plainWords', () => {
  it('a bare dtc_planning (dtc_critical_unserved effect)', () => {
    expect(plainWords('size what islanded operation needs (dtc_planning)'))
      .toBe('size what islanded operation needs (a plan for running without the grid)')
  })

  it('the VOLL effect of a not-established finding', () => {
    expect(plainWords('set VOLL to 5000 €/MWh (frontier and fmea_top need VOLL > 0)'))
      .toBe('set the price of undelivered energy to 5000 €/MWh (the cost-versus-reliability check '
        + 'and the top-risk check need a price above zero)')
  })

  it('frontier and fmea_top on their own', () => {
    expect(plainWords('re-run with the frontier stage')).toBe('re-run with the cost-versus-reliability check step')
    expect(plainWords('fmea_top: not established')).toBe('Top risks could not be worked out')
    expect(plainWords('the fmea_top ranking')).toBe('the top-risk check ranking')
  })

  it('the combined grid-loss phrase, N+1 and ‱', () => {
    expect(plainWords('(dtc_stress + dtc_planning)'))
      .toBe('(a grid-loss test and a plan for running without the grid)')
    expect(plainWords('an N+1 unit at ENS 2.5 ‱'))
      .toBe('a spare unit at unserved energy 2.5 parts per 10 000')
  })

  // P26 walkthrough (island microgrid, inconclusive): the Improve card read
  // "re-run the simulation with 1000 draws (was 500) to narrow the confidence
  // interval" — "draws" and "confidence interval" are statistics jargon.
  it('the inconclusive re-run effect (MC draws, confidence interval)', () => {
    expect(plainWords('re-run the MC with 1000 draws (was 500) to narrow the confidence interval'))
      .toBe('re-run the simulation with 1000 runs (was 500) to narrow the range of the estimate')
  })

  // P26 gate friction 3: finding titles end in "— outage-driven".
  it('outage-driven', () => {
    expect(plainWords('Not certified: LOLE 12.38 h/yr exceeds the 3 h/yr target — outage-driven'))
      .toBe('Not certified: expected shortfall 12.38 h/yr exceeds the 3 h/yr target — caused by equipment outages')
  })

  // P30 (C11, R8): the lone rule, with nothing around it.
  it('a lone dtc_planning effect', () => {
    expect(plainWords('dtc_planning')).toBe('a plan for running without the grid')
    expect(plainWords('add dtc_planning to the study'))
      .toBe('add a plan for running without the grid to the study')
  })

  // P30 (C12): "Critical demand unserved when … is lost: 12.5 MWh" kept the
  // unit; a number of MWh reads in words. A price per MWh is left alone.
  it('a number of MWh reads as megawatt-hours', () => {
    expect(plainWords('Critical demand unserved when grid_import is lost: 12.5 MWh'))
      .toBe('Critical demand unserved when grid_import is lost: 12.5 megawatt-hours')
    expect(plainWords('lost: 1,234.5 MWh')).toBe('lost: 1,234.5 megawatt-hours')
    expect(plainWords('3 MWh or 40 MWh')).toBe('3 megawatt-hours or 40 megawatt-hours')
    expect(plainWords('set VOLL to 5000 €/MWh')).toBe('set the price of undelivered energy to 5000 €/MWh')
    expect(plainWords('12 MWhx')).toBe('12 MWhx')
  })

  it('evidence values are rounded, lists too', () => {
    expect(evidenceValue(12.383928571428568)).toBe('12.38')
    expect(evidenceValue([7.68316774385459, 17.084689399002553])).toBe('[7.683, 17.08]')
    expect(evidenceValue(500)).toBe('500')
    expect(evidenceValue('fail')).toBe('fail')
  })
})
