// guided-results 04, slice 2: Guided and Expert print each result figure under
// the one name in backend/services/figure_words.json (mode parity).
import { describe, expect, it } from 'vitest'
import { figureBasis, figureHint, figureName, figureTag, figureUnit } from './figureWords'
import { HEADLINES } from '../pages/results/investment/financeModel'
import { HEADLINE_WORDS, headlineFigure } from '../pages/results/headlineWords'
import { chapterTiles, NO_TAX_PACK_BASIS, tileHint, type ChapterInputs } from '../pages/hubDesign/chapters'
import type { HeadlineCard } from '../api/simulation'
import source from '../../../backend/services/figure_words.json'

const NONE: ChapterInputs = {
  investment: { study: null, report: null },
  grid: { campus: null, notHub: false },
  reliability: { hasResults: false, running: false, lole: null, target: null, verdict: null },
  operation: { status: null, gfm: null },
}
const tiles = (inputs: Partial<ChapterInputs>) =>
  Object.fromEntries(chapterTiles({ ...NONE, ...inputs }).map(t => [t.id, t]))
const label = (id: string) => HEADLINES.find(h => h.id === id)!.label

describe('the one source', () => {
  it('the frontend reads exactly these figures', () => {
    expect(Object.keys(source.figures).sort()).toEqual([
      'carrier_lcoe', 'expected_shortfall', 'lcoe_nominal', 'lcoe_real', 'project_npv', 'project_npv_pre_tax',
      'screening_shortfall', 'study_cost_per_mwh',
    ])
  })

  it('holds the ruled words (GK B (63), GR04-SHORTFALL-1)', () => {
    expect(figureName('project_npv')).toBe('Project NPV at the WACC, after tax (unlevered)')
    expect(figureName('project_npv_pre_tax')).toBe('Project NPV at the WACC, before tax (unlevered)')
    expect(figureName('expected_shortfall')).toBe('Expected shortfall')
    expect(figureUnit('expected_shortfall')).toBe('h/yr')
    expect(figureTag('expected_shortfall')).toBe('LOLE')
    expect(figureHint('expected_shortfall').match(/LOLE/g)).toHaveLength(1)
    expect(figureName('lcoe_real')).toBe('LCOE (real)')
    expect(figureName('lcoe_nominal')).toBe('LCOE (finance-consistent, nominal)')
  })

  it('never fills a missing currency with a silent euro', () => {
    expect(figureUnit('lcoe_real')).toBe('report currency/MWh')
    expect(figureUnit('lcoe_real', 'USD')).toBe('USD/MWh')
  })
})

describe('Guided and Expert name a figure alike', () => {
  it('NPV: the Investment tab and the Guided tile, under the lead line', () => {
    const report = { npv_at_wacc: 1.5e6, sections: {} } as never
    const t = tiles({ investment: { study: null, report } }).investment
    expect(label('npv_at_wacc')).toBe(figureName('project_npv'))
    expect(t.label).toBe(figureName('project_npv'))
    expect(t.lead).toBe('What the design is worth')
    // a tile with no figure keeps both
    expect(tiles({}).investment).toMatchObject({ label: figureName('project_npv'), lead: 'What the design is worth' })
  })

  it('NPV with no tax pack: the pre-tax name, and the one sentence for the cause', () => {
    const report = { npv_at_wacc: null, sections: { project: { payload: { project_pre_tax_npv: -2e6 } } } } as never
    const t = tiles({ investment: { study: null, report } }).investment
    expect(t).toMatchObject({ label: figureName('project_npv_pre_tax'), lead: 'What the design is worth' })
    expect(t.basis).toBe(NO_TAX_PACK_BASIS)
    expect(NO_TAX_PACK_BASIS).toBe('No tax pack, so the after-tax figure is not established.')
  })

  it("Compare's per-carrier cost is not called the investment case's LCOE", () => {
    expect(figureName('carrier_lcoe')).toBe('Carrier LCOE (cash cost)')
    expect(figureName('carrier_lcoe')).not.toBe(figureName('lcoe_real'))
    expect(figureBasis('carrier_lcoe')).toMatch(/per MWh the carrier produced/)
  })

  it('LCOE: the Investment tab, the Expert strip and Guided headline (one Words entry)', () => {
    expect(label('lcoe')).toBe(figureName('lcoe_nominal'))
    expect(label('lcoe_real')).toBe(figureName('lcoe_real'))
    expect(HEADLINE_WORDS.lcoe.label).toBe(figureName('lcoe_real'))
  })

  it("LCOE: the card's own basis line, with its money year", () => {
    const card = { id: 'lcoe', value: 77.5, unit: 'USD/MWh', state: 'ready', reason: null, formula_id: null,
      detail: null, basis: 'investment case · real basis, 2024 USD' } as HeadlineCard
    expect(headlineFigure(card).basis).toBe('investment case · real basis, 2024 USD')
    expect(headlineFigure({ ...card, basis: undefined }).basis).toBe('investment case')
  })

  it('shortfall: the Guided tile is named and unit-ed by the source, with LOLE on hover', () => {
    const t = tiles({ reliability: { hasResults: true, running: false, lole: 1.2, target: 3, verdict: 'pass' } })
      .reliability
    expect(t.label).toBe(figureName('expected_shortfall'))
    expect(t.unit).toBe(figureUnit('expected_shortfall'))
    expect(t.label).not.toMatch(/LOLE/)
    expect(tileHint(t)).toBe(figureHint('expected_shortfall'))
    expect(tileHint({ ...t, fromStudy: true })).toMatch(/LOLE.* From the study/)
  })
})
