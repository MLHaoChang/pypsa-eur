import { describe, expect, it } from 'vitest'
import { isRenewableCarrier } from './carriers'

describe('isRenewableCarrier', () => {
  it('matches the renewable keyword list case-insensitively', () => {
    for (const c of ['wind', 'Solar', 'PV', 'offwind-ac', 'onwind', 'ror',
                     'run-of-river', 'geothermal', 'solar-rooftop']) {
      expect(isRenewableCarrier(c), c).toBe(true)
    }
  })

  it('is null-safe', () => {
    // The whole reason this helper exists: the predicate was copy-pasted into
    // four components, two of which called carrier.toLowerCase() directly and
    // crashed on a null carrier. Any regression here reintroduces that crash.
    expect(() => isRenewableCarrier(null)).not.toThrow()
    expect(() => isRenewableCarrier(undefined)).not.toThrow()
    expect(isRenewableCarrier(null)).toBe(false)
    expect(isRenewableCarrier(undefined)).toBe(false)
    expect(isRenewableCarrier('')).toBe(false)
  })

  it('counts plain hydro as renewable', () => {
    expect(isRenewableCarrier('hydro')).toBe(true)
    expect(isRenewableCarrier('Hydro Reservoir')).toBe(true)
  })

  it('excludes pumped/stored hydro — those belong to Storage', () => {
    expect(isRenewableCarrier('pumped hydro')).toBe(false)
    expect(isRenewableCarrier('hydro pump')).toBe(false)
    expect(isRenewableCarrier('hydro storage')).toBe(false)
    expect(isRenewableCarrier('PHS hydro-storage')).toBe(false)
  })

  it('rejects thermal and carrier-less rows', () => {
    for (const c of ['gas', 'coal', 'lignite', 'nuclear', 'oil', 'biomass', 'AC', 'DC']) {
      expect(isRenewableCarrier(c), c).toBe(false)
    }
  })
})
