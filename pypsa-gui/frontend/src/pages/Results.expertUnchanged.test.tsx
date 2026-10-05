import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'

// Guided-mode spec §3.5 / §3.9: in Expert mode the Results tab strip is the
// base commit's strip — same tabs, same order, same markup. The snapshot was
// recorded before P23 touched Results.tsx; the `results-tab-<id>` test ids
// (§3.5) are the only allowed difference and are stripped before comparing.
// Re-recorded once for a deliberate addition: the Expert-only Investment tab
// (Edge Investment Case P3 WP3.5), between FMEA and Asset Detail.
const NEW_TEST_IDS = /\s?data-testid="results-tab-[a-z]+"/g
function normalise(html: string): string {
  return html.replace(NEW_TEST_IDS, '')
}

const { stub } = vi.hoisted(() => ({
  stub: (testid: string) => ({ default: () => <div data-testid={testid} /> }),
}))

vi.mock('./results/CapacityExpansion', () => stub('capex-stub'))
vi.mock('./results/Dispatch', () => stub('dispatch-stub'))
vi.mock('./results/LoadFlow', () => stub('loadflow-stub'))
vi.mock('./results/Prices', () => stub('prices-stub'))
vi.mock('./results/Emissions', () => stub('emissions-stub'))
vi.mock('./results/Economics', () => stub('economics-stub'))
vi.mock('./results/AggregatedOverview', () => stub('aggregated-stub'))
vi.mock('./results/Curtailment', () => stub('curtailment-stub'))
vi.mock('./results/LostLoadTab', () => stub('lostload-stub'))
vi.mock('./results/AdequacyTab', () => stub('adequacy-stub'))
vi.mock('./results/FmeaTab', () => stub('fmea-stub'))
vi.mock('./results/StorageCycling', () => stub('storage-stub'))
vi.mock('./results/asset/AssetDetail', () => stub('asset-stub'))
vi.mock('./results/InvestmentTab', () => stub('investment-stub'))
vi.mock('./CompareView', () => ({ default: () => <div data-testid="compare-stub" /> }))

vi.mock('../api/simulation', () => ({
  simulationApi: { getStatus: vi.fn().mockResolvedValue({ running: false }) },
  resultsApi: {
    getGeneratorResults: vi.fn().mockResolvedValue({}),
    getSummary: vi.fn().mockResolvedValue({}),
  },
}))
vi.mock('../api/network', () => ({
  networkApi: {
    getSnapshots: vi.fn().mockResolvedValue({ snapshots: [], count: 0 }),
    getInvestmentPeriods: vi.fn().mockResolvedValue({ periods: [] }),
  },
}))

import Results from './Results'

async function renderStrip() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <Results />
    </QueryClientProvider>,
  )
  await act(async () => { await new Promise(r => setTimeout(r, 20)) })
  // The strip is the parent of the tab buttons; anchor on a stable title.
  return screen.getByTitle('Per-snapshot generation, storage, loads').parentElement as HTMLElement
}

beforeEach(() => {
  useUIStore.setState({ uiMode: 'expert', currentProject: 'Demo', compareRailOpen: false })
})

describe('Results tab strip — Expert unchanged', () => {
  it('renders the base strip with the default tab', async () => {
    const strip = await renderStrip()
    expect(normalise(strip.outerHTML)).toMatchSnapshot()
  })

  it('renders the base strip with a stored non-guided tab active', async () => {
    localStorage.setItem('results:active-tab', 'prices')
    const strip = await renderStrip()
    expect(normalise(strip.outerHTML)).toMatchSnapshot()
    expect(screen.getByTestId('prices-stub')).toBeTruthy()
  })
})
