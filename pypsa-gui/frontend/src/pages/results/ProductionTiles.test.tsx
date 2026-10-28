// Ticket 07: the Production tiles render from a /results/process_view payload, and are absent
// when the network has no product bus.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useUIStore } from '../../store/uiStore'
const nav = vi.hoisted(() => ({ openFlowTarget: vi.fn(), openFlowTargetInPlace: vi.fn() }))
vi.mock('../../layout/flowNavigation', () => nav)
import ProductionTiles from './ProductionTiles'
import type { ProcessAmount, ProcessProduct, ProcessView } from '../../api/types'

afterEach(cleanup)

const amount = (t: number | null, extra: Partial<ProcessAmount> = {}): ProcessAmount =>
  ({ value: t, unit: 'MWh', t, flag: null, ...extra })

const NH3: ProcessProduct = {
  bus: 'nh3', carrier: 'NH3', unit: 'MWh', t_per_unit: 1 / 5.166,
  production: amount(1200), delivery: amount(1000), buy_in: amount(0),
  lost: amount(40, { eur: 32_000 }),
  inventory: [{ name: 'nh3_tank', unit: 'MWh', e: [0, 100, 516.6], flag: null }],
}

const view = (products: ProcessProduct[]): ProcessView => ({ products, process_units: [] })

describe('ProductionTiles', () => {
  it('shows made, delivered, lost, lost EUR and closing inventory', () => {
    render(<ProductionTiles view={view([NH3])} />)
    const val = (id: string) => screen.getByTestId(`production-nh3-${id}`).querySelector('[data-kpi-value]')?.getAttribute('data-kpi-value')
    expect(val('made')).toBe('1.20 kt')
    expect(val('delivered')).toBe('1.00 kt')
    expect(val('lost')).toBe('40.0 t')
    expect(val('lost-eur')).toBe('€32.0 k')
    expect(val('inventory')).toBe('100 t')
  })

  it('shows a state tile, not a zero, for a figure the view could not compute', () => {
    const p: ProcessProduct = { ...NH3, t_per_unit: null,
      production: amount(1200, { t: null, t_flag: 'tonne_factor_missing' }),
      lost: amount(null, { t: null, flag: 'no_shortfall_slack', eur: null, eur_flag: 'lost_production_value_missing' }),
      inventory: [] }
    render(<ProductionTiles view={view([p])} />)
    expect(screen.getByTestId('production-nh3-made').getAttribute('data-state')).toBe('not_modelled')
    const fix = screen.getByTestId('production-nh3-fix')
    expect(fix.textContent).toMatch(/Energy per tonne/)
    expect(fix.textContent).not.toMatch(/mwh_per_t/)
    expect(screen.getAllByText('Set it on nh3 ›')).toHaveLength(1)
    expect(screen.getByTestId('production-nh3-lost-eur').getAttribute('data-state')).toBe('not_modelled')
    expect(screen.getByTestId('production-nh3-inventory').getAttribute('data-state')).toBe('not_applicable')
  })

  it('renders nothing for a network with no product bus', () => {
    const { container } = render(<ProductionTiles view={view([])} />)
    expect(container.innerHTML).toBe('')
    cleanup()
    expect(render(<ProductionTiles view={undefined} />).container.innerHTML).toBe('')
  })

  it.each([['guided', 'openFlowTargetInPlace', 'openFlowTarget'], ['expert', 'openFlowTarget', 'openFlowTargetInPlace']] as const)(
    'the link opens the bus field through the %s opener', (mode, used, other) => {
      nav.openFlowTarget.mockClear(); nav.openFlowTargetInPlace.mockClear()
      useUIStore.setState({ uiMode: mode })
      const p: ProcessProduct = { ...NH3, t_per_unit: null, production: amount(1, { t: null, t_flag: 'tonne_factor_missing' }) }
      render(<ProductionTiles view={view([p])} />)
      fireEvent.click(screen.getByText('Set it on nh3 ›'))
      expect(nav[used]).toHaveBeenCalledWith({ section: 'carriers', panel: null, component: 'Bus', entity: 'nh3', field: 'mwh_per_t' })
      expect(nav[other]).not.toHaveBeenCalled()
    })
})
