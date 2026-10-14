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
// Re-recorded again for UX overhaul S3 (owner decision D1a): the strip is now
// a group row (Summary · Investment · Grid plan · Reliability · Operation,
// Asset detail on the right) plus a row holding the active group's tabs. The
// snapshot covers both rows; test ids are still stripped.
const NEW_TEST_IDS = /\s?data-testid="results-(tab|group)-[a-z_]+"/g
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
  // The group row is the parent of the group buttons; the sub-row (absent
  // when Summary is a one-tab group) follows it.
  const groups = screen.getByTestId('results-group-summary').parentElement as HTMLElement
  const sub = document.querySelector('[data-testid="results-subtabs"]')
  const wrap = document.createElement('div')
  wrap.append(groups.cloneNode(true))
  if (sub) wrap.append(sub.cloneNode(true))
  return wrap
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
