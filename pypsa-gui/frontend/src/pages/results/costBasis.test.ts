// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

// Template-e2e 18: every cost figure states its basis, and the LP objective says why it is not the total.
import { describe, expect, it } from 'vitest'
import type { ObjectiveDecomposition } from '../../api/results'
import {
  gridSupplyWords, objectiveFigure, objectiveBasis, objectiveConstant, objectiveGapReasons, objectiveName, totalCostBasis,
} from './costBasis'

const m = (v: number) => `€${(v / 1e6).toFixed(1)} M`

const dec = (over: Partial<ObjectiveDecomposition> = {}): ObjectiveDecomposition => ({
  n_objective: 120.29e6, n_objective_constant: 0, lp_total: 120.29e6, cost_breakdown_total: 194.87e6,
  gap_eur: -74.57e6, lp_total_scope: 'horizon', nonextendable_fixed_cost_eur: 6.61e6,
  period_weighting_adjustment_eur: -67.97e6, residual_gap_eur: 0, discount: { ref_year: 2027, real_rate: 0.049 },
  ...over,
})

describe('the run figure names its basis', () => {
  it('is the LP objective, discounted to its first period or not', () => {
    expect(objectiveName({ objective_basis: 'lp_objective', objective_discounted_to: 2027 })).toBe('LP objective')
    expect(objectiveBasis({ objective_basis: 'lp_objective', objective_discounted_to: 2027 })).toBe('discounted to 2027')
    expect(objectiveBasis({ objective_basis: 'lp_objective', objective_discounted_to: null })).toBe('not discounted')
    // An older backend sends no basis: the figure it reports is the LP objective.
    expect(objectiveName(undefined)).toBe('LP objective')
  })

  it('says a result solved before the basis was recorded does not know it', () => {
    const st = { objective_basis: 'unknown' as const, objective_discounted_to: null }
    expect(objectiveName(st)).toBe('Objective')
    expect(objectiveBasis(st)).toMatch(/not recorded/)
  })

  it('is the system cost under myopic foresight and rolling horizon', () => {
    const st = { objective_basis: 'system_cost' as const, objective_discounted_to: null }
    expect(objectiveName(st)).toBe('System cost')
    expect(objectiveBasis(st)).toMatch(/not discounted/)
  })

  it('says a multi-period total is a sum not discounted', () => {
    expect(totalCostBasis(true)).toBe('in this solve, summed over the periods, not discounted')
    expect(totalCostBasis(false)).toBe('in this solve')
  })
})

const words = (d: ObjectiveDecomposition) => objectiveGapReasons(d, m).map(r => r.words)

describe('objectiveGapReasons', () => {
  it('names the discounting and the existing assets, each saying which way it moves the figure (H2 staged)', () => {
    expect(words(dec())).toEqual([
      'discounting to 2027 at 4.9 % real takes €68.0 M off',
      "existing assets' fixed cost (€6.6 M) is not in it",
    ])
  })

  it('adds up: the total plus every named amount is the LP objective, within rounding', () => {
    for (const d of [dec(), dec({ reserve_revenue_eur: 2e6, residual_gap_eur: -4.6e6,
      lp_total: 194.87e6 - 67.97e6 - 6.61e6 - 2e6 - 4.6e6 })]) {
      const sum = objectiveGapReasons(d, m).reduce((acc, r) => acc + r.amount, d.cost_breakdown_total as number)
      expect(Math.abs(sum - (d.lp_total as number))).toBeLessThan(0.01e6)
    }
  })

  it('says nothing when the two agree, or when there is no single LP total to compare', () => {
    expect(objectiveGapReasons(dec({ lp_total: 194.87e6 }), m)).toEqual([])
    expect(objectiveGapReasons(dec({ lp_total_scope: 'last_period' }), m)).toEqual([])
    expect(objectiveGapReasons(dec({ cost_breakdown_total: null }), m)).toEqual([])
    expect(objectiveGapReasons(null, m)).toEqual([])
  })

  it('names weights that are not a discount (no stamp, or none applied), reserve revenue and other LP terms', () => {
    const base = { nonextendable_fixed_cost_eur: 0, reserve_revenue_eur: 2e6, residual_gap_eur: -4.6e6 }
    const expected = [
      "weighting the periods by the objective's weights, not their years, takes €68.0 M off",
      'reserve revenue takes €2.0 M off',
      'other terms the optimiser counts take €4.6 M off',
    ]
    expect(words(dec({ ...base, discount: null }))).toEqual(expected)
    expect(words(dec({ ...base, discount: 'unknown' }))).toEqual(expected)
  })

  it('leaves out a reason below half a percent of the total', () => {
    expect(objectiveGapReasons(dec({ nonextendable_fixed_cost_eur: 0.5e6 }), m)).toHaveLength(1)
  })
})

describe('objectiveConstant', () => {
  it("is the existing assets' fixed cost a single-period objective carries (H2 hub: Guided 7.61 M, Expert 9.02 M)", () => {
    const flat = dec({ n_objective: 7.609e6, n_objective_constant: 1.413e6, lp_total: 9.022e6,
      cost_breakdown_total: 9.022e6, discount: null, nonextendable_fixed_cost_eur: 0, period_weighting_adjustment_eur: 0 })
    expect(objectiveConstant(flat)).toBe(1.413e6)
    expect(objectiveGapReasons(flat, m)).toEqual([])
    expect(objectiveConstant(dec())).toBeNull()
  })
})

describe('the grid supply note (Key capacities)', () => {
  const conn = (mw: number | null, refusal: string | null = null) =>
    ({ state: 'complete' as const, connection_limit: { mw, refusal, links: ['grid_import'] } })
  it("names the server's connection limit, to one decimal (the data centre's 40 MW import Link)", () => {
    expect(gridSupplyWords(conn(40))).toBe("Grid supply: up to 40.0 MW, through the site's connection.")
    expect(gridSupplyWords(conn(1250))).toBe("Grid supply: up to 1,250.0 MW, through the site's connection.")
  })
  it('says an off-grid site imports nothing, whatever its tie is rated (the island microgrid)', () => {
    expect(gridSupplyWords({ state: 'not_needed', connection_limit: { mw: 20, refusal: null, links: ['tie'] } }))
      .toBe('Grid supply: none, the site runs off the grid.')
  })
  it("says the server's reason where the network fixes no limit, and nothing until the flow is read", () => {
    expect(gridSupplyWords(conn(null, 'The connection limit is 0 MW: set one or choose another site type.')))
      .toBe('Grid supply is not listed: The connection limit is 0 MW: set one or choose another site type.')
    expect(gridSupplyWords({ state: 'complete' })).toMatch(/^Grid supply is not listed: what the site can import/)
    expect(gridSupplyWords(null)).toBeNull()
  })
})

describe("the run's figure (status bar and toast)", () => {
  it("is money()'s notation, signed with the minus before the symbol (ruling (ad))", () => {
    expect(objectiveFigure(120_290_000)).toBe('€\u00a0120\u00a0m')
    expect(objectiveFigure(-16_310_000)).toBe('-€\u00a016\u00a0m')
    expect(objectiveFigure(-830_400)).toBe('-€\u00a0830\u00a0k')
    expect(objectiveFigure(null)).toBeNull()
    expect(objectiveFigure(Number.NaN)).toBeNull()
  })
})
