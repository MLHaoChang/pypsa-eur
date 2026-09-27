import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'

// Guided-mode spec §3.5 / §3.7 — Results in Guided shows only the adequacy
// and FMEA tabs (`expertOnly` on every other row, filtered like `multiOnly`).
// A stored tab Guided does not show is COERCED to adequacy for display and
// never written back, so switching to Expert restores it.

const { stub, snapState } = vi.hoisted(() => ({
  stub: (testid: string) => ({ default: () => <div data-testid={testid} /> }),
  snapState: { periods: undefined as number[] | undefined },
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
    getSnapshots: vi.fn(async () => ({
      snapshots: snapState.periods ? ['2030-01-01T00:00:00', '2040-01-01T00:00:00'] : [],
      count: snapState.periods ? 2 : 0,
      periods: snapState.periods,
    })),
    getInvestmentPeriods: vi.fn().mockResolvedValue({ periods: [] }),
  },
}))

import Results from './Results'

const ALL_IDS = ['overview', 'capex', 'dispatch', 'loadflow', 'prices', 'economics', 'emissions',
  'curtailment', 'lostload', 'adequacy', 'storage', 'fmea', 'asset']

async function renderResults() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const utils = render(
    <QueryClientProvider client={client}>
      <Results />
    </QueryClientProvider>,
  )
  await act(async () => { await new Promise(r => setTimeout(r, 20)) })
  return utils
}

function tabIds(): string[] {
  return Array.from(document.querySelectorAll('[data-testid^="results-tab-"]'))
    .map(el => el.getAttribute('data-testid')!.slice('results-tab-'.length))
}

beforeEach(() => {
  snapState.periods = undefined
  useUIStore.setState({ currentProject: 'Demo', compareRailOpen: false, resultsTabRequest: null })
})

describe('Results in Guided mode', () => {
  beforeEach(() => useUIStore.setState({ uiMode: 'guided' }))

  it('shows exactly the adequacy and FMEA tabs', async () => {
    await renderResults()
    expect(tabIds()).toEqual(['adequacy', 'fmea'])
  })

  it('shows exactly two tabs on a multi-period run too', async () => {
    snapState.periods = [2030, 2040]
    await renderResults()
    expect(tabIds()).toEqual(['adequacy', 'fmea'])
  })

  it('a stored hidden tab renders adequacy and is not rewritten', async () => {
    localStorage.setItem('results:active-tab', 'dispatch')
    await renderResults()
    expect(screen.getByTestId('adequacy-stub')).toBeTruthy()
    expect(screen.queryByTestId('dispatch-stub')).toBeNull()
    expect(screen.getByTestId('results-tab-adequacy').className).toContain('border-accent')
    expect(localStorage.getItem('results:active-tab')).toBe('dispatch')
  })

  it('a stored fmea tab is kept', async () => {
    localStorage.setItem('results:active-tab', 'fmea')
    await renderResults()
    expect(screen.getByTestId('fmea-stub')).toBeTruthy()
  })

  it('switching back to Expert restores the stored tab', async () => {
    localStorage.setItem('results:active-tab', 'dispatch')
    await renderResults()
    expect(screen.getByTestId('adequacy-stub')).toBeTruthy()
    act(() => { useUIStore.setState({ uiMode: 'expert' }) })
    expect(screen.getByTestId('dispatch-stub')).toBeTruthy()
    expect(screen.getByTestId('results-tab-dispatch').className).toContain('border-accent')
  })

  it('clicking FMEA switches and persists as usual', async () => {
    await renderResults()
    fireEvent.click(screen.getByTestId('results-tab-fmea'))
    expect(screen.getByTestId('fmea-stub')).toBeTruthy()
    expect(localStorage.getItem('results:active-tab')).toBe('fmea')
  })
})

describe('Results in Expert mode', () => {
  beforeEach(() => useUIStore.setState({ uiMode: 'expert' }))

  it('single-period: every tab except the multi-period Overview, in order', async () => {
    await renderResults()
    expect(tabIds()).toEqual(ALL_IDS.filter(id => id !== 'overview'))
  })

  it('multi-period: all thirteen tabs, in order', async () => {
    snapState.periods = [2030, 2040]
    await renderResults()
    expect(await screen.findByTestId('results-tab-overview')).toBeTruthy()
    expect(tabIds()).toEqual(ALL_IDS)
  })

  it('a stored dispatch tab renders dispatch', async () => {
    localStorage.setItem('results:active-tab', 'dispatch')
    await renderResults()
    expect(screen.getByTestId('dispatch-stub')).toBeTruthy()
  })
})
