// UX overhaul D3 rows 19–25: the per-carrier Dispatch panel says why a figure
// is missing — not applicable, not modelled or a computed zero — instead of
// "—" or a bare 0 MWh.
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import type { ComponentProps } from 'react'
import { CarrierKpiPanel } from './Dispatch'
import type { Generator, StorageUnit } from '../../api/types'
import type { TSPayload } from './shared'

afterEach(cleanup)

const gen = (name: string, carrier: string, extra: Partial<Generator> = {}): Generator => ({
  name, bus: 'B', carrier, p_nom: 100,
  p_nom_extendable: false, p_nom_min: 0, p_nom_max: null,
  p_min_pu: 0, p_max_pu: 1, control: 'PQ',
  marginal_cost: 0, capital_cost: 0, fom_cost: 0,
  overnight_cost: null, discount_rate: null, curtailment_cost: 0,
  efficiency: 1, committable: false,
  ramp_limit_up: null, ramp_limit_down: null,
  start_up_cost: 0, shut_down_cost: 0, min_up_time: 0, min_down_time: 0,
  e_sum_min: null, e_sum_max: null,
  build_year: 0, lifetime: null, unit: 'MW',
  ...extra,
})

const battery: StorageUnit = {
  name: 'Bat', bus: 'B', carrier: 'battery', p_nom: 10,
  p_nom_extendable: false, p_nom_min: 0, p_nom_max: null,
  max_hours: 4, efficiency_store: 1, efficiency_dispatch: 1,
  standing_loss: 0, cyclic_state_of_charge: false, state_of_charge_initial: 0,
  inflow: 0, capital_cost: 0, marginal_cost: 0, fom_cost: 0,
  overnight_cost: null, discount_rate: null, build_year: 0, lifetime: null,
}

const ts = (cols: string[], row: number[]): TSPayload =>
  ({ index: ['2026-01-01T00:00:00'], columns: cols, data: [row] }) as TSPayload

const bucket = (total: number) => ({ total, by_period: {} })
const econ = (dispatchGwh: number) => ({
  revenue_meur: bucket(0), opex_meur: bucket(0.001), gen_cost_meur: bucket(0.001),
  storage_charge_cost_meur: bucket(0), curtailment_cost_meur: bucket(0), lost_load_cost_meur: bucket(0),
  capex_meur: bucket(0.002), dispatch_gwh: bucket(dispatchGwh), lcoe_eur_per_mwh: bucket(0),
})

type Props = ComponentProps<typeof CarrierKpiPanel>

function renderPanel(over: Partial<Props> = {}) {
  const props: Props = {
    carrier: 'ac',
    group: { gens: new Set(['Gas']), loads: new Set(), storage: new Set(), stores: new Set(), busNames: new Set(['B']) },
    gensTS: ts(['Gas'], [10]), loadTS: null, storPowerTS: null, storeTS: null,
    curtailTS: null, linkTS: null,
    generators: [gen('Gas', 'gas')], loads: [], storageUnits: [], stores: [], links: [],
    weightCtx: {} as Props['weightCtx'],
    range: { from: 0, to: 0 },
    lostLoadByCarrier: null,
    econByCarrier: { gas: econ(0.01) },
    scaled: false, avgScale: 1, isElectrical: true,
    ...over,
  }
  return render(<CarrierKpiPanel {...props} />)
}

const tile = (label: string) => screen.getByText(label, { selector: 'div' }).closest('[data-state]') as HTMLElement

describe('CarrierKpiPanel — result states', () => {
  it('says storage, link supply, curtailment and their costs do not apply to a carrier without them', () => {
    renderPanel()
    for (const label of ['Storage discharge', 'Storage charge', 'Gen — links', 'Curtailment',
      'Storage charge cost', 'Curtailment cost']) {
      expect(tile(label).getAttribute('data-state'), label).toBe('not_applicable')
      expect(tile(label).querySelector('[data-kpi-value]'), label).toBeNull()
    }
    expect(screen.queryByText('—')).toBeNull()
  })

  it('shows computed zeros as zeros when the carrier has storage and renewables', () => {
    renderPanel({
      group: { gens: new Set(['Gas', 'PV']), loads: new Set(), storage: new Set(['Bat']), stores: new Set(), busNames: new Set(['B']) },
      gensTS: ts(['Gas', 'PV'], [10, 5]),
      curtailTS: ts(['PV'], [0]),
      storPowerTS: ts(['Bat'], [0]),
      generators: [gen('Gas', 'gas'), gen('PV', 'solar')],
      storageUnits: [battery],
      econByCarrier: { gas: econ(0.015) },
    })
    for (const label of ['Storage discharge', 'Storage charge', 'Curtailment', 'Storage charge cost']) {
      expect(tile(label).getAttribute('data-state'), label).toBe('zero')
    }
  })

  it('says curtailment cost was not modelled when no renewable on the carrier has one', () => {
    renderPanel({
      group: { gens: new Set(['PV']), loads: new Set(), storage: new Set(), stores: new Set(), busNames: new Set(['B']) },
      gensTS: ts(['PV'], [5]), curtailTS: ts(['PV'], [2]),
      generators: [gen('PV', 'solar')],
      econByCarrier: { solar: econ(0.005) },
    })
    expect(tile('Curtailment cost').getAttribute('data-state')).toBe('not_modelled')
  })

  it('says LCOE does not apply when nothing was dispatched on the carrier', () => {
    renderPanel({ gensTS: ts(['Gas'], [0]), econByCarrier: { gas: econ(0) } })
    expect(tile('LCOE').getAttribute('data-state')).toBe('not_applicable')
  })

  it('says lost load was not modelled when the solve had no VOLL', () => {
    renderPanel({ lostLoadNotModelled: true })
    expect(tile('Lost load').getAttribute('data-state')).toBe('not_modelled')
    expect(tile('Lost-load cost').getAttribute('data-state')).toBe('not_modelled')
  })

  it('shows lost load as a computed zero when VOLL was set and nothing was shed', () => {
    renderPanel()
    expect(tile('Lost load').getAttribute('data-state')).toBe('zero')
  })
})
