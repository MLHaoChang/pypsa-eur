import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { resultsApi } from '../../api/simulation'
import ProductsTab, { averageOutput, runningShare } from './ProductsTab'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return { ...actual, resultsApi: { ...actual.resultsApi, getProcessView: vi.fn(), getLcoh: vi.fn() } }
})

const amt = (t: number | null) => ({ value: t, unit: 'MWh', t, flag: null })
const product = (bus: string, carrier: string, t: number) => ({
  bus, carrier, unit: 'MWh', t_per_unit: 0.03, production: amt(t), delivery: amt(t), buy_in: { ...amt(0), carriers: [] },
  lost: { ...amt(0), eur: 0, carriers: [] }, inventory: [],
})

beforeEach(() => { useUIStore.setState({ currentProject: 'Demo' }) })
afterEach(() => { cleanup(); vi.clearAllMocks() })

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><ProductsTab /></QueryClientProvider>)
}

it('hydrogen: kg/h and the LCOH read verbatim (equals the report figure)', async () => {
  vi.mocked(resultsApi.getProcessView).mockResolvedValue({
    products: [product('H2 bus', 'H2', 240)],
    process_units: [{ name: 'electrolyser', product_bus: 'H2 bus', running_hours: 6, weighted_hours: 8 }],
  } as never)
  vi.mocked(resultsApi.getLcoh).mockResolvedValue({ rows: [], total: { lcoh_eur_per_kg_h2: 4.2561 }, currency: 'EUR' } as never)
  mount()
  expect((await screen.findByTestId('products-H2 bus-cost')).textContent).toContain('4.26')
  expect(screen.getByTestId('products-H2 bus-cost').textContent).toContain('€/kg')
  expect(screen.getByTestId('products-H2 bus-rate').textContent).toContain('kg/h')
  expect(screen.getByTestId('products-electrolyser-running').textContent).toContain('75')
  expect(screen.queryByTestId('products-H2 bus-no-cost')).toBeNull()
})

it('ammonia: t/d output and a plain note that no cost per tonne exists', async () => {
  vi.mocked(resultsApi.getProcessView).mockResolvedValue({
    products: [product('NH3 bus', 'NH3', 600)],
    process_units: [{ name: 'haber', product_bus: 'NH3 bus', running_hours: 24, weighted_hours: 24 }],
  } as never)
  vi.mocked(resultsApi.getLcoh).mockResolvedValue(null as never)
  mount()
  expect((await screen.findByTestId('products-NH3 bus-rate')).textContent).toContain('t/d')
  expect(screen.getByTestId('products-NH3 bus-no-cost').textContent).toMatch(/No cost per tonne/)
  expect(screen.queryByTestId('products-NH3 bus-cost')).toBeNull()
})

it('averageOutput and runningShare are unit changes of read figures, null when a figure is missing', () => {
  expect(averageOutput(product('b', 'H2', 24) as never, 24)).toEqual({ value: '1000.0', unit: 'kg/h' })
  expect(averageOutput(product('b', 'NH3', 1) as never, 24)).toEqual({ value: '1.0', unit: 't/d' })
  expect(averageOutput(product('b', 'NH3', 1) as never, null)).toBeNull()
  expect(runningShare({ name: 'u', running_hours: 3, weighted_hours: 12 })).toBe(25)
  expect(runningShare({ name: 'u', running_hours: null, weighted_hours: 12 })).toBeNull()
})

it('a failed process view says "not available", never a blank or a zero', async () => {
  vi.mocked(resultsApi.getProcessView).mockRejectedValue(new Error('409'))
  vi.mocked(resultsApi.getLcoh).mockResolvedValue(null as never)
  mount()
  expect((await screen.findByTestId('products-unavailable')).textContent).toMatch(/not available/i)
})

it('a failed LCOH read shows the hydrogen cost as not available with the reason, never 0', async () => {
  vi.mocked(resultsApi.getProcessView).mockResolvedValue({
    products: [product('H2 bus', 'H2', 240)],
    process_units: [{ name: 'electrolyser', product_bus: 'H2 bus', running_hours: 6, weighted_hours: 8 }],
  } as never)
  vi.mocked(resultsApi.getLcoh).mockRejectedValue(new Error('404'))
  mount()
  const cost = await screen.findByTestId('products-H2 bus-cost')
  await vi.waitFor(() => expect(cost.textContent).not.toMatch(/\b0(\.0+)?\s*€\/kg/))
  expect(screen.getByTestId('products-H2 bus-made').textContent).toContain('240')
})
