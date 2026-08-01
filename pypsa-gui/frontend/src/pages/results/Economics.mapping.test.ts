import { describe, it, expect } from 'vitest'
import golden from './__fixtures__/asset-economics.golden.json'
import { makeGenRow, makeLinkRow, makeSURow } from './Economics'
import type { GeneratorEconomicsRow, LinkEconomicsRow, StorageUnitEconomicsRow } from '../../api/simulation'

// The backend can be perfectly self-consistent while the frontend maps the
// wrong field. A Link deliberately puts GROSS revenue in `revenue_eur` and the
// energy it bought in `charge_cost_eur`, reusing the storage columns — swap
// those two and the tab shows a wildly wrong net profit while all nine backend
// surfaces still agree with each other.
//
// Rows are selected by `name`, not array index — the fixture has already been
// regenerated twice during this plan, and index access would silently start
// asserting against the wrong asset on the next reorder.

const gas = (golden.generators as GeneratorEconomicsRow[]).find(g => g.name === 'gas')!
const electrolyzer = (golden.links as LinkEconomicsRow[]).find(l => l.name === 'electrolyzer')!
const bess = (golden.storage_units as StorageUnitEconomicsRow[]).find(s => s.name === 'bess')!

describe('asset economics row mapping', () => {
  it('carries a link’s gross revenue and input cost into the storage columns', () => {
    const row = makeLinkRow(electrolyzer)

    expect(row.group).toBe('Converters')
    expect(row.revenue_eur).toBe(electrolyzer.gross_revenue_eur)
    expect(row.charge_cost_eur).toBe(electrolyzer.input_cost_eur)
    // NOT the net figure — that lives in net_profit_eur.
    expect(row.revenue_eur).not.toBe(electrolyzer.revenue_eur)
  })

  it('carries a link’s OUTPUT as energy, never its input', () => {
    const row = makeLinkRow(electrolyzer)

    expect(row.energy_mwh).toBe(electrolyzer.energy_mwh)
    expect(row.charge_mwh).toBe(electrolyzer.input_energy_mwh)
    expect(row.energy_mwh).not.toBe(electrolyzer.input_energy_mwh)
  })

  it('preserves generator cost fields exactly', () => {
    const row = makeGenRow(gas)

    expect(row.fixed_cost_eur).toBe(gas.fixed_cost_eur)
    expect(row.vom_cost_eur).toBe(gas.vom_cost_eur)
    expect(row.net_profit_eur).toBe(gas.net_profit_eur)
    expect(row.charge_cost_eur).toBe(0)
  })

  // `bess` is the only storage unit in the fixture, and it's inert: the
  // golden LP prices every snapshot flat (no spread to arbitrage), so bess
  // never cycles and every energy/cost field on it — discharge_mwh,
  // charge_mwh, discharge_revenue_eur, charge_cost_eur, vom_cost_eur,
  // fixed_cost_eur, fom_cost_eur, net_profit_eur — is genuinely 0. A cost
  // assertion on any of those fields can't discriminate a correct mapping
  // from a hardcoded 0 or a wrong-field read, because every candidate value
  // is also 0. capacity_label is built from p_nom_opt_mw and
  // energy_capacity_mwh instead, which ARE non-zero on this asset, so a
  // wrong-field read or a swapped pair here genuinely fails.
  it('derives a storage unit’s capacity label from its non-zero capacity fields', () => {
    const row = makeSURow(bess)

    expect(row.capacity_label).toBe('50.0 MW · 200 MWh')
  })

  // WEAK BY CONSTRUCTION (see comment above): this only proves a real zero
  // survives the mapping unmangled — not NaN, not a stray nonzero leak. It
  // cannot distinguish a correct mapping from a hardcoded 0, because bess's
  // fixed_cost_eur is 0 in the fixture regardless of how it's computed. The
  // capacity_label test above is what actually exercises makeSURow's field
  // wiring for this asset.
  it('preserves a zero-cost storage unit as zero', () => {
    const row = makeSURow(bess)

    expect(row.fixed_cost_eur).toBe(0)
  })
})
