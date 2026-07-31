import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { resultsApi } from '../../api/simulation'
import { networkApi } from '../../api/network'
import Prices from './Prices'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getPrices: vi.fn(), getEmissions: vi.fn(), getPriceDrivers: vi.fn(),
    },
  }
})

vi.mock('../../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/network')>()
  return { ...actual, networkApi: { ...actual.networkApi, getBuses: vi.fn() } }
})

afterEach(() => cleanup())

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  // Single bus, single snapshot: mean/peak/trough of one point are all that
  // same point, regardless of the exact busStats aggregation implementation.
  vi.mocked(resultsApi.getPrices).mockReset().mockResolvedValue({
    index: ['2026-01-01T00:00:00'], columns: ['Bus 0'], data: [[424.24]],
  })
  // getEmissions' real return type (api/simulation.ts:260-306) requires
  // by_generator/cap/caps/is_multi_period/by_period beyond what the brief
  // supplied; the brief's literal transcription fails tsc AND crashes at
  // runtime (`emissions?.cap.active` throws on a missing `cap`). Filled
  // with inert defaults — `cap.active: false` keeps the CO2-shadow-price KPI
  // hidden, and Prices.tsx never reads by_generator/caps/is_multi_period/by_period.
  vi.mocked(resultsApi.getEmissions).mockReset().mockResolvedValue({
    total_tCO2: 0, by_carrier: [], by_generator: [],
    cap: { active: false }, caps: [], is_multi_period: false, by_period: [],
  })
  // getPriceDrivers' real return type is an object ({threshold,
  // total_above_threshold, truncated, rows}), not a bare array. The brief's
  // `[]` fails tsc AND crashes at runtime: Prices.tsx:470 reads
  // `priceDrivers.rows.length` unconditionally once `priceDrivers` is
  // truthy, and an array has no `.rows`. Fixed to the real empty shape.
  vi.mocked(resultsApi.getPriceDrivers).mockReset().mockResolvedValue({
    threshold: 2000, total_above_threshold: 0, truncated: false, rows: [],
  })
  // Bus (api/types.ts:1-4) requires x/y/country/unit/control/sub_network
  // beyond what the brief supplied. Prices.tsx only reads `carrier`/`v_nom`
  // off a bus row, so these six are inert padding to satisfy the type.
  vi.mocked(networkApi.getBuses).mockReset().mockResolvedValue([
    { name: 'Bus 0', v_nom: 380, carrier: 'AC',
      x: 0, y: 0, country: '', unit: '', control: 'PQ', sub_network: '' },
  ])
})

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <Prices />
    </QueryClientProvider>,
  )
}

it('renders a distinctive per-bus price sourced from a single mocked bus/snapshot', async () => {
  renderPage()
  // The brief's single-match `findByText('424.24')` throws
  // "Found multiple elements" here: the per-bus table's Mean/Peak/Trough
  // columns coincide on a one-snapshot fixture (confirmed by running it —
  // see task-20-report.md), exactly the collision the brief's own comment
  // above predicts. Using findAllByText (the same fix Task 18's Dispatch
  // brief applied for the identical coincidence) keeps the assertion tied
  // to the fixture's 424.24 without depending on how many cells repeat it.
  const matches = await screen.findAllByText('424.24')
  expect(matches.length).toBeGreaterThan(0)
})
