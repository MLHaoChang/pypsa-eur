// The zero-profit-by-construction caveat reaches the reader of the tab.
//
// An extendable asset at an interior optimum earns ≈ zero net profit BY
// CONSTRUCTION. The backend now says so in `reading_notes` whenever the
// payload holds such an asset; this pins that the tab renders the sentence
// beside the net-profit KPI, and renders nothing when there is nothing to
// say (a fixed fleet is not an equilibrium, and a caveat there would invent
// a cause).
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { resultsApi } from '../../api/simulation'
import Economics from './Economics'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getAssetEconomics: vi.fn(),
      getLcoh: vi.fn(),
    },
  }
})

const NOTE = 'Zero-profit equilibrium: an extendable asset at an interior optimum '
  + 'earns approximately zero net profit BY CONSTRUCTION — the LP builds until '
  + 'the marginal MW breaks even. A near-zero net profit here is the expected '
  + 'result, not a fault.'

function payload(reading_notes: string[] | undefined) {
  return {
    currency: 'EUR',
    is_multi_period: false,
    capital_costs_available: true,
    periods: [],
    generators: [
      {
        name: 'Gas', bus: 'B1', carrier: 'gas',
        p_nom_opt_mw: 100, energy_mwh: 424.24, capacity_factor: 0.5,
        revenue_eur: 1000, vom_cost_eur: 100, fixed_cost_eur: 900,
        fom_cost_eur: 0, net_profit_eur: 0, lcoe_eur_per_mwh: 50,
        avg_price_eur_per_mwh: null, by_period: [],
      },
    ],
    storage_units: [],
    stores: [],
    links: [],
    ...(reading_notes ? { reading_notes } : {}),
  }
}

afterEach(() => cleanup())
beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  vi.mocked(resultsApi.getLcoh).mockReset().mockResolvedValue({ rows: [], total: null, currency: 'EUR' })
})

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <Economics />
    </QueryClientProvider>,
  )
}

it('shows the backend reading note beside the KPIs', async () => {
  vi.mocked(resultsApi.getAssetEconomics).mockReset().mockResolvedValue(payload([NOTE]) as never)
  renderPage()
  const chip = await screen.findByTestId('economics-reading-notes')
  expect(chip.textContent).toContain('BY CONSTRUCTION')
})

it('renders no note when the backend sends none', async () => {
  vi.mocked(resultsApi.getAssetEconomics).mockReset().mockResolvedValue(payload(undefined) as never)
  renderPage()
  await screen.findAllByText('Net profit')
  expect(screen.queryByTestId('economics-reading-notes')).toBeNull()
})

// The cross-surface golden payload (written by the backend's golden test)
// carries the note because its `gas` and `electrolyzer` are interior. This
// makes the recorded contract live: the string the chip shows IS the
// backend's string, not a third copy typed here.
import golden from './__fixtures__/asset-economics.golden.json'

it('renders the golden payload\'s own note', async () => {
  vi.mocked(resultsApi.getAssetEconomics).mockReset().mockResolvedValue(golden as never)
  renderPage()
  const chip = await screen.findByTestId('economics-reading-notes')
  expect(golden.reading_notes.length).toBeGreaterThan(0)
  expect(chip.textContent).toBe(golden.reading_notes.join(' '))
})
