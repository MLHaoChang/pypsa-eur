import { describe, it, expect } from 'vitest'
import golden from './__fixtures__/asset-economics.golden.json'
import { makeGenRow, makeLinkRow, makeSURow } from './Economics'
import type { GeneratorEconomicsRow, LinkEconomicsRow, StorageUnitEconomicsRow } from '../../api/simulation'

// The backend can be perfectly self-consistent while the frontend maps the
// wrong field. A Link deliberately puts GROSS revenue in `revenue_eur` and the
// energy it bought in `charge_cost_eur`, reusing the storage columns — swap
// those two and the tab shows a wildly wrong net profit while all nine backend
// surfaces still agree with each other.

describe('asset economics row mapping', () => {
  it('carries a link’s gross revenue and input cost into the storage columns', () => {
    const link = (golden.links as LinkEconomicsRow[])[0]
    const row = makeLinkRow(link)

    expect(row.group).toBe('Converters')
    expect(row.revenue_eur).toBe(link.gross_revenue_eur)
    expect(row.charge_cost_eur).toBe(link.input_cost_eur)
    // NOT the net figure — that lives in net_profit_eur.
    expect(row.revenue_eur).not.toBe(link.revenue_eur)
  })

  it('carries a link’s OUTPUT as energy, never its input', () => {
    const link = (golden.links as LinkEconomicsRow[])[0]
    const row = makeLinkRow(link)

    expect(row.energy_mwh).toBe(link.energy_mwh)
    expect(row.charge_mwh).toBe(link.input_energy_mwh)
    expect(row.energy_mwh).not.toBe(link.input_energy_mwh)
  })

  it('preserves generator cost fields exactly', () => {
    const gen = (golden.generators as GeneratorEconomicsRow[])[0]
    const row = makeGenRow(gen)

    expect(row.fixed_cost_eur).toBe(gen.fixed_cost_eur)
    expect(row.vom_cost_eur).toBe(gen.vom_cost_eur)
    expect(row.net_profit_eur).toBe(gen.net_profit_eur)
    expect(row.charge_cost_eur).toBe(0)
  })

  it('preserves a zero-cost storage unit as zero', () => {
    const su = (golden.storage_units as StorageUnitEconomicsRow[])[0]
    const row = makeSURow(su)

    expect(row.fixed_cost_eur).toBe(0)
  })
})
