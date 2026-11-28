import { describe, expect, it } from 'vitest'
import type { HeadlineCard } from '../../../api/simulation'
import { sameFigureCaveat } from './InvestmentCaseView'

/** te-24 (LCOE-CAVEAT-1): Investment's "LCOE (real)" carries the headline card's caveat only
 *  when the card holds that same figure (UX rule 46: Expert never says less than Guided). */
const card = (over: Partial<HeadlineCard> = {}): HeadlineCard => ({
  id: 'lcoe', value: 413, unit: 'EUR/MWh', state: 'ready', reason: null, formula_id: null,
  detail: null, delta: null, trend: null, caveat: 'Above € 300/MWh, high for a hospital.', ...over,
})

describe('sameFigureCaveat', () => {
  it('passes the caveat on for the same figure', () => {
    expect(sameFigureCaveat(card(), 413)).toBe('Above € 300/MWh, high for a hospital.')
    expect(sameFigureCaveat(card(), 413 + 1e-9)).toBe('Above € 300/MWh, high for a hospital.')
  })
  it('says nothing for another figure, a card without a caveat or figure, or a stale card', () => {
    expect(sameFigureCaveat(card(), 412)).toBeNull()
    expect(sameFigureCaveat(card({ caveat: null }), 413)).toBeNull()
    expect(sameFigureCaveat(card({ value: null }), 413)).toBeNull()
    expect(sameFigureCaveat(card({ state: 'stale' }), 413)).toBeNull()
    expect(sameFigureCaveat(card(), null)).toBeNull()
    expect(sameFigureCaveat(undefined, 413)).toBeNull()
  })
})
