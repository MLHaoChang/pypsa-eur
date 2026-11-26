// Assistant-assurance 17, slice 2b: on an integer (MIP) solve the API withholds the
// prices (and the generator-cost fallback) with the reason. The Prices tab says so
// in words only: no chart skeleton, no 0, no "—", no NaN. An LP still draws.
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { resultsApi } from '../../api/simulation'
import { networkApi } from '../../api/network'
import Prices from './Prices'
import { withheldBlock } from './sectionWords'

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

// The tab reads the prices and their drivers as one bound view (A5.2 S3b); it
// carries what the two mocked routes return.
vi.mock('../../api/displayedResults', async () => {
  const { resultsApi } = await import('../../api/simulation')
  return {
    displayedResultsApi: {
      prices: (threshold: number, limit: number, range?: { from: number; to: number }) => Promise.all([
        resultsApi.getPrices('lopf', range), resultsApi.getPriceDrivers(threshold, limit),
      ]).then(([prices, drivers]) => prices && {
        status: 'ok', data: { prices, price_drivers: drivers ?? null }, binding: { version: 1, project_id: 'p', scope: 'current', view: 'prices', data_sha256: 'a'.repeat(64) },
      }),
    },
  }
})
vi.mock('../../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/network')>()
  return {
    ...actual,
    networkApi: { ...actual.networkApi, getBuses: vi.fn(), getSnapshots: vi.fn() },
  }
})

const WITHHELD = { status: 'unavailable', reason: 'mip_duals_not_marginal' }

afterEach(() => cleanup())
beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  vi.mocked(resultsApi.getPrices).mockReset().mockResolvedValue(WITHHELD)
  vi.mocked(resultsApi.getEmissions).mockReset().mockResolvedValue({
    total_tCO2: 0, by_carrier: [], by_generator: [],
    cap: { active: false }, caps: [], is_multi_period: false, by_period: [],
  })
  vi.mocked(resultsApi.getPriceDrivers).mockReset().mockResolvedValue(WITHHELD as never)
  vi.mocked(networkApi.getBuses).mockReset().mockResolvedValue([
    { name: 'Bus 0', v_nom: 380, carrier: 'AC',
      x: 0, y: 0, country: '', unit: '', control: 'PQ', sub_network: '' },
  ])
  vi.mocked(networkApi.getSnapshots).mockReset().mockResolvedValue({
    count: 1, snapshots: ['2026-01-01T00:00:00'], weightings: [], ts_start: null, ts_end: null,
    can_sample_weeks: false,
  })
})

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><Prices /></QueryClientProvider>)
}

it('says in words that a MIP has no marginal prices, with no chart and no number', async () => {
  const { container } = renderPage()
  const text = withheldBlock('Marginal prices', 'mip_duals_not_marginal')
  expect(text).toBe('Marginal prices: not available. An integer (MIP) solve has no marginal prices.')
  expect(await screen.findByText(text)).toBeTruthy()
  expect(container.querySelector('svg.recharts-surface')).toBeNull()
  expect(screen.queryByText(/Approximated prices/)).toBeNull()  // the fallback banner
  expect(screen.queryByText('Price drivers')).toBeNull()
  expect(container.textContent).not.toMatch(/NaN|—|€\s?0\b|\b0\.00\b/)
  // The bound view reads the drivers with the prices, and they come back withheld too.
})

it('still draws an LP\'s prices', async () => {
  vi.mocked(resultsApi.getPrices).mockResolvedValue({
    index: ['2026-01-01T00:00:00'], columns: ['Bus 0'], data: [[424.24]],
  })
  renderPage()
  expect(await screen.findAllByText(/424\.24/)).not.toHaveLength(0)
  expect(screen.queryByText(/not available/)).toBeNull()
})

it('keeps the withheld words and state from the bound view, never "not run" or "unavailable" (UX 31)', async () => {
  const { container } = renderPage()
  expect(await screen.findByText(withheldBlock('Marginal prices', 'mip_duals_not_marginal'))).toBeTruthy()
  expect(container.textContent).not.toMatch(/not run|has not run|could not be read|unavailable/i)
  expect(screen.queryByTestId('prices-error')).toBeNull()
  // The withheld answer is what the view bound: the root carries that binding.
  const root = container.querySelector('[data-result-tab="prices"]')!
  expect(JSON.parse(root.getAttribute('data-result-binding')!).view).toBe('prices')
})
