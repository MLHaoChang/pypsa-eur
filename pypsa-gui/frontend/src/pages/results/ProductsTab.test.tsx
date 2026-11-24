import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { resultsApi } from '../../api/simulation'
import type { ProcessAmount, ProcessProduct, ProcessUnit, ProcessView } from '../../api/types'
import ProductsTab, { averageOutput, runningShare } from './ProductsTab'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return { ...actual, resultsApi: { ...actual.resultsApi, getProcessView: vi.fn(), getLcoh: vi.fn() } }
})

type Lcoh = NonNullable<Awaited<ReturnType<typeof resultsApi.getLcoh>>>
const amt = (t: number | null): ProcessAmount => ({ value: t, unit: 'MWh', t, flag: null })
const product = (bus: string, carrier: string, t: number): ProcessProduct => ({
  bus, carrier, unit: 'MWh', t_per_unit: 0.03, production: amt(t), delivery: amt(t),
  buy_in: { ...amt(0), carriers: [] }, lost: { ...amt(0), eur: 0, carriers: [] }, inventory: [],
})
const unit = (name: string, bus: string, running: number | null, weighted: number | null): ProcessUnit =>
  ({ name, product_bus: bus, running_hours: running, weighted_hours: weighted })
const view = (products: ProcessProduct[], process_units: ProcessUnit[]): ProcessView => ({ products, process_units })
const period = (p: number) => ({ period: p, h2_produced_mwh: 1, capex_eur: 1, vom_cost_eur: 1, electricity_cost_eur: 1,
  lcoh_eur_per_mwh_h2: 1, lcoh_eur_per_kg_h2: 4.2561 })
const lcoh = (kg: number, by_period?: Lcoh['total'] extends infer T ? (T extends { by_period?: infer B } ? B : never) : never): Lcoh => ({
  rows: [], currency: 'EUR',
  total: { h2_produced_mwh: 1, capex_eur_per_year: 1, vom_cost_eur: 1, electricity_cost_eur: 1, lcoh_eur_per_mwh_h2: 1,
    lcoh_eur_per_kg_h2: kg, by_period },
})
const H2_VIEW = view([product('H2 bus', 'H2', 240)], [unit('electrolyser', 'H2 bus', 6, 8)])

beforeEach(() => { useUIStore.setState({ currentProject: 'Demo' }) })
afterEach(() => { cleanup(); vi.clearAllMocks() })

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><ProductsTab /></QueryClientProvider>)
}

it('hydrogen: kg/h and the LCOH read verbatim (equals the report figure)', async () => {
  vi.mocked(resultsApi.getProcessView).mockResolvedValue(H2_VIEW)
  vi.mocked(resultsApi.getLcoh).mockResolvedValue(lcoh(4.2561))
  mount()
  expect((await screen.findByTestId('products-H2 bus-cost')).textContent).toContain('4.26')
  expect(screen.getByTestId('products-H2 bus-cost').textContent).toContain('€/kg')
  expect(screen.getByTestId('products-H2 bus-rate').textContent).toContain('kg/h')
  expect(screen.getByTestId('products-electrolyser-running').textContent).toContain('75')
  expect(screen.queryByTestId('products-H2 bus-no-cost')).toBeNull()
})

it('ammonia: t/d output and a plain note that no cost per tonne exists', async () => {
  vi.mocked(resultsApi.getProcessView).mockResolvedValue(view([product('NH3 bus', 'NH3', 600)], [unit('haber', 'NH3 bus', 24, 24)]))
  vi.mocked(resultsApi.getLcoh).mockResolvedValue(null)
  mount()
  expect((await screen.findByTestId('products-NH3 bus-rate')).textContent).toContain('t/d')
  expect(screen.getByTestId('products-NH3 bus-no-cost').textContent).toMatch(/No cost per tonne/)
  expect(screen.queryByTestId('products-NH3 bus-cost')).toBeNull()
})

it('averageOutput and runningShare are unit changes of read figures, null when a figure is missing', () => {
  expect(averageOutput(product('b', 'H2', 24), 24)).toEqual({ value: '1000.0', unit: 'kg/h' })
  expect(averageOutput(product('b', 'NH3', 1), 24)).toEqual({ value: '1.0', unit: 't/d' })
  expect(averageOutput(product('b', 'NH3', 1), null)).toBeNull()
  // two uneven periods (backend test_two_uneven_periods...): 240 t over 90 weighted hours = 64.0 t/d
  expect(averageOutput(product('b', 'NH3', 240), 90)).toEqual({ value: '64.0', unit: 't/d' })
  expect(runningShare(unit('u', 'b', 3, 12))).toBe(25)
  expect(runningShare(unit('u', 'b', null, 12))).toBeNull()
})

it('a failed process view says "not available", never a blank or a zero', async () => {
  vi.mocked(resultsApi.getProcessView).mockRejectedValue(new Error('409'))
  vi.mocked(resultsApi.getLcoh).mockResolvedValue(null)
  mount()
  expect((await screen.findByTestId('products-unavailable')).textContent).toMatch(/not available/i)
})

it('a failed LCOH read shows the hydrogen cost as not available with the reason, never 0', async () => {
  vi.mocked(resultsApi.getProcessView).mockResolvedValue(H2_VIEW)
  vi.mocked(resultsApi.getLcoh).mockRejectedValue(new Error('404'))
  mount()
  await vi.waitFor(() => expect(screen.getByTestId('products-H2 bus-cost').textContent).toContain('Not available: the hydrogen cost could not be read'))
  expect(screen.getByTestId('products-H2 bus-cost').textContent).not.toMatch(/\b0(\.0+)?\s*€\/kg/)
  expect(screen.getByTestId('products-H2 bus-made').textContent).toContain('240')
})

it('no hydrogen produced (lcoh total null) says so, never 0 €/kg', async () => {
  vi.mocked(resultsApi.getProcessView).mockResolvedValue(H2_VIEW)
  vi.mocked(resultsApi.getLcoh).mockResolvedValue({ rows: [], total: null, currency: 'EUR' })
  mount()
  await vi.waitFor(() => expect(screen.getByTestId('products-H2 bus-cost').textContent).toContain('no hydrogen produced by an electrolyser'))
  expect(screen.getByTestId('products-H2 bus-cost').textContent).not.toContain('€/kg')
})

it('the cost basis is the server\'s: by_period listed reads across all periods, absent reads the modelled year', async () => {
  vi.mocked(resultsApi.getProcessView).mockResolvedValue(H2_VIEW)
  vi.mocked(resultsApi.getLcoh).mockResolvedValue(lcoh(4.2561, [period(2030), period(2040)]))
  const first = mount()
  await vi.waitFor(() => expect(screen.getByTestId('products-H2 bus-cost').textContent).toContain('across all investment periods'))
  expect(screen.getByTestId('products-H2 bus-cost').textContent).not.toContain('modelled year')
  first.unmount()
  vi.mocked(resultsApi.getLcoh).mockResolvedValue(lcoh(4.2561))
  mount()
  await vi.waitFor(() => expect(screen.getByTestId('products-H2 bus-cost').textContent).toContain('for the modelled year'))
  expect(screen.getByTestId('products-H2 bus-cost').textContent).not.toContain('across all')
})
