import { describe, expect, it } from 'vitest'
import { CARRIER_CATALOG, CARRIER_CATALOG_NAMES } from './carrierCatalog'
import { carrierMatches } from './busCarriers'

describe('carrier catalogue: process products and heat', () => {
  const byName = (n: string) => CARRIER_CATALOG.find(c => c.name === n)

  it('counts NH3 in MWh and the materials in tonnes (design D2)', () => {
    expect(byName('NH3')?.unit).toBe('MWh')
    for (const m of ['N2', 'ore', 'concentrate']) expect(byName(m)?.unit).toBe('t')
  })

  it('leaves every earlier carrier without a unit', () => {
    for (const c of CARRIER_CATALOG) {
      if (!['NH3', 'N2', 'ore', 'concentrate'].includes(c.name)) expect(c.unit, c.name).toBeUndefined()
    }
  })

  it('offers a carrier the form\'s heat-bus filter accepts', () => {
    expect(CARRIER_CATALOG_NAMES.filter(n => carrierMatches(n, 'heat'))).toEqual(['heat'])
  })
})
