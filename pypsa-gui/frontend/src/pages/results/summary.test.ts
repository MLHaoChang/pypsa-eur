// M3: the Summary tab's arithmetic (summary.ts). Every figure is an endpoint
// field or one named subtraction; these pin the scope rules (horizon vs a
// selected period) and the unnamed PyPSA 1.1 statistics index columns.
import { describe, expect, it } from 'vitest'
import type { CostBreakdown } from '../../api/simulation'
import { capacityRows, costView, emissionsView, reserveMarginVerdict, vollOf, type EmissionsPayload } from './summary'

const COST = {
  capex: 900, capex_expansion: 300, opex: 100, total: 1000,
  by_period: [
    { period: 2030, capex: 400, opex: 60, total: 460, by_component: [] },
    { period: 2040, capex: 500, opex: 40, total: 540, by_component: [] },
  ],
} as unknown as CostBreakdown

describe('costView', () => {
  it('reads the horizon totals, with the new-build capital', () => {
    expect(costView(COST, null)).toEqual({ total: 1000, capital: 900, operating: 100, newBuild: 300, period: null })
  })
  it('reads the selected period, where new build is not split per period', () => {
    expect(costView(COST, '2040')).toEqual({ total: 540, capital: 500, operating: 40, newBuild: null, period: 2040 })
  })
  it('no breakdown, no view', () => {
    expect(costView(null, null)).toBeNull()
  })
})

const EMISSIONS: EmissionsPayload = {
  total_tCO2: 5000,
  caps: [
    { name: 'co2_h', investment_period: null, scope: 'horizon', cap_tCO2: 6000, used_tCO2: 5000, shadow_price_eur_per_tCO2: 0, binding: false },
    { name: 'co2_2030', investment_period: 2030, scope: 'period', cap_tCO2: 3000, used_tCO2: 3000, shadow_price_eur_per_tCO2: 42, binding: true },
    { name: 'co2_2040', investment_period: 2040, scope: 'period', cap_tCO2: 2500, used_tCO2: 2000, shadow_price_eur_per_tCO2: 0, binding: false },
  ],
  by_period: [{ period: 2030, total_tCO2: 3000 }, { period: 2040, total_tCO2: 2000 }],
}

describe('emissionsView', () => {
  it('the horizon shows the total and every cap', () => {
    const v = emissionsView(EMISSIONS, null)!
    expect(v.total_tCO2).toBe(5000)
    expect(v.caps.map(c => c.name)).toEqual(['co2_h', 'co2_2030', 'co2_2040'])
  })
  it('a period shows its total, its own cap and the horizon cap', () => {
    const v = emissionsView(EMISSIONS, 2030)!
    expect(v.total_tCO2).toBe(3000)
    expect(v.caps.map(c => c.name)).toEqual(['co2_h', 'co2_2030'])
  })
})

describe('capacityRows', () => {
  it('reads PyPSA 1.1 rows (unnamed index columns), keeps generators, storage and stores, largest first', () => {
    const rows = capacityRows([
      { level_0: 'Generator', level_1: 'gas', 'Optimal Capacity': 100, 'Installed Capacity': 100 },
      { level_0: 'Generator', level_1: 'solar', 'Optimal Capacity': 166.7, 'Installed Capacity': 10 },
      { level_0: 'Load', level_1: '-', 'Optimal Capacity': 0, 'Installed Capacity': 0 },
      { level_0: 'Line', level_1: 'AC', 'Optimal Capacity': 500, 'Installed Capacity': 500 },
      { level_0: 'StorageUnit', level_1: 'battery', 'Optimal Capacity': 5, 'Installed Capacity': 5 },
      { level_0: 'Store', level_1: 'h2', 'Optimal Capacity': 50, 'Installed Capacity': 20 },
    ], null)
    expect(rows.map(r => [r.carrier, r.unit, r.capacity, r.newBuild])).toEqual([
      ['solar', 'MW', 166.7, 156.7], ['gas', 'MW', 100, 0], ['battery', 'MW', 5, 0], ['h2', 'MWh', 50, 30],
    ])
  })
  it('new build is never negative: an extendable asset optimised below its starting size built nothing', () => {
    const [r] = capacityRows([{ level_0: 'Generator', level_1: 'wind', 'Optimal Capacity': 4, 'Installed Capacity': 10 }], null)
    expect(r.newBuild).toBe(0)
  })
  it('multi-period: the selected period, else the last period (the fleet at the end of the horizon)', () => {
    const stats = [{
      level_0: 'Generator', level_1: 'solar',
      'Optimal Capacity | 2030': 20, 'Installed Capacity | 2030': 10,
      'Optimal Capacity | 2040': 50, 'Installed Capacity | 2040': 10,
    }]
    expect(capacityRows(stats, null)[0]).toMatchObject({ capacity: 50, newBuild: 40 })
    expect(capacityRows(stats, 2030)[0]).toMatchObject({ capacity: 20, newBuild: 10 })
  })
})

describe('reserveMarginVerdict', () => {
  it('says how many periods missed the margin', () => {
    expect(reserveMarginVerdict({ margin: 0.15, by_period: [{ period: 1, met: true, binding: false }] }))
      .toEqual({ met: true, text: 'met' })
    expect(reserveMarginVerdict({ margin: 0.15, by_period: [
      { period: 2030, met: true, binding: false }, { period: 2040, met: false, binding: true },
    ] })).toEqual({ met: false, text: 'not met in 1 of 2 periods' })
  })
  it('no margin set, no verdict', () => {
    expect(reserveMarginVerdict(null)).toBeNull()
  })
})

// A1.7: VOLL is the payload's own, else cost / MWh from two known totals with
// MWh > 0 — never a division by zero, never 0 for an unknown.
describe('vollOf', () => {
  it('prefers the payload VOLL, a real 0 included', () => {
    expect(vollOf({ voll_eur_per_mwh: 3000, total_mwh: 2, total_cost_eur: 10 })).toBe(3000)
    expect(vollOf({ voll_eur_per_mwh: 0, total_mwh: 2, total_cost_eur: 10 })).toBe(0)
  })
  it('derives cost / MWh only from two known totals with MWh > 0', () => {
    expect(vollOf({ voll_eur_per_mwh: null, total_mwh: 4, total_cost_eur: 6000 })).toBe(1500)
    expect(vollOf({ voll_eur_per_mwh: null, total_mwh: 0, total_cost_eur: 50 })).toBeNull()
    expect(vollOf({ voll_eur_per_mwh: null, total_mwh: null, total_cost_eur: 50 })).toBeNull()
    expect(vollOf({ voll_eur_per_mwh: null, total_mwh: 4, total_cost_eur: null })).toBeNull()
    expect(vollOf(null)).toBeNull()
  })
})
