import { describe, expect, it } from 'vitest'
import { REVENUE_EXCEEDS_COST, headlineFigure, totalCostWords } from './headlineWords'
import type { HeadlineCard } from '../../api/simulation'

// Template-e2e 14: the IPP's negative objective read "Total system cost -€4.55 M".
describe('totalCostWords', () => {
  it('says a negative total is revenue over cost, with a real minus sign and no "profit"', () => {
    const w = totalCostWords(-4_550_000)
    expect(w.value).toBe('−€4.55 M')
    expect(w.note).toBe('(revenue exceeds cost)')
    expect(`${w.value} ${w.note}`).not.toMatch(/profit|NPV/i)
  })
  it('leaves a positive or zero total, and an unknown one, without a note', () => {
    expect(totalCostWords(1_200_000)).toEqual({ value: '€1.20 M' })
    expect(totalCostWords(0)).toEqual({ value: '€0.00' })
    expect(totalCostWords(null)).toEqual({ value: '—' })
  })
  it('is what the strip shows for the system cost card, and only for it', () => {
    const card = { id: 'system_cost', unit: 'currency', value: -4_550_000 } as HeadlineCard
    expect(headlineFigure(card)).toEqual({ value: '−€4.55 M', basis: `in this solve ${REVENUE_EXCEEDS_COST}` })
    expect(headlineFigure({ ...card, value: 10 })).toEqual({ value: '€10.00', basis: 'in this solve' })
    expect(headlineFigure({ ...card, id: 'other' })).toEqual({ value: '€-4.55 M', basis: 'in this solve' })
  })
})
