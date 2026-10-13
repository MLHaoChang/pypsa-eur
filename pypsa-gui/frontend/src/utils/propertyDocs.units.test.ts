// Every capital-cost tooltip names the annuity's unit per year, and every
// overnight-cost tooltip names the upfront unit without `/yr`. The tooltip is
// where a reader checks which of the two numbers a field wants, so a tooltip
// that says "€/MVA" on the annuity repeats the mislabel S0 removed from the
// badges (gate S0 [S2], follow-up F1).
import { describe, expect, it } from 'vitest'
import { PROPERTY_DOCS } from './propertyDocs'

const UNIT = /€\/(MWh|MW|MVA)(\/yr)?/

function entries(field: string): Array<[string, string]> {
  return Object.entries(PROPERTY_DOCS).filter(([k]) => k.endsWith(`.${field}`))
}

describe('propertyDocs cost units', () => {
  it('covers every component that carries a capital cost', () => {
    expect(entries('capital_cost').map(([k]) => k.split('.')[0]).sort())
      .toEqual(['generator', 'line', 'link', 'storage_unit', 'store'])
  })

  it.each(entries('capital_cost'))('%s names a unit per year', (_key, text) => {
    const unit = text.match(UNIT)
    expect(unit, text).not.toBeNull()
    expect(unit?.[2]).toBe('/yr')
  })

  it.each(entries('overnight_cost'))('%s names an upfront unit, not per year', (_key, text) => {
    const unit = text.match(UNIT)
    expect(unit, text).not.toBeNull()
    expect(unit?.[2]).toBeUndefined()
  })
})
