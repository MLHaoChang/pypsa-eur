// M3 (plan 2026-10-14-m3-results-summary.md): Results opens on Summary after
// each new solve, then keeps the tab the user picks until the next solve; a
// tab asked for explicitly (the assistant's results_tab) is not overridden.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'

const { stub, status } = vi.hoisted(() => ({
  stub: (testid: string) => ({ default: () => <div data-testid={testid} /> }),
  status: { current: { running: false, status: 'ok', condition: 'optimal', objective: 100, solve_time: 1.5 } as Record<string, unknown> },
}))

vi.mock('./results/SummaryTab', () => stub('summary-stub'))
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
vi.mock('./CompareView', () => stub('compare-stub'))
vi.mock('../api/simulation', () => ({
  simulationApi: { getStatus: vi.fn(async () => status.current) },
  resultsApi: { getGeneratorResults: vi.fn().mockResolvedValue({}) },
}))
vi.mock('../api/network', () => ({
  networkApi: {
    getSnapshots: vi.fn(async () => ({ snapshots: [], count: 0 })),
    getInvestmentPeriods: vi.fn().mockResolvedValue({ periods: [] }),
  },
}))

import Results from './Results'

async function renderResults() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const utils = render(<QueryClientProvider client={client}><Results /></QueryClientProvider>)
  await act(async () => { await new Promise(r => setTimeout(r, 20)) })
  return utils
}

beforeEach(() => {
  localStorage.clear()
  status.current = { running: false, status: 'ok', condition: 'optimal', objective: 100, solve_time: 1.5 }
  useUIStore.setState({ currentProject: 'Demo', compareRailOpen: false, resultsTabRequest: null, uiMode: 'expert' })
})

describe('Results opens on Summary after a new solve (M3)', () => {
  it('a solve not seen yet opens Summary, even over a remembered tab', async () => {
    localStorage.setItem('results:active-tab', 'dispatch')
    await renderResults()
    expect(screen.getByTestId('summary-stub')).toBeTruthy()
  })

  it('the same solve keeps the tab the user picked', async () => {
    const first = await renderResults()
    fireEvent.click(screen.getByTestId('results-group-capacity'))   // UX S3: tabs sit under groups
    fireEvent.click(screen.getByTestId('results-cardtab-costs_prices'))
    first.unmount()
    await renderResults()
    expect(screen.getByTestId('prices-stub')).toBeTruthy()
  })

  it('the next solve opens Summary again', async () => {
    const first = await renderResults()
    fireEvent.click(screen.getByTestId('results-group-capacity'))   // UX S3: tabs sit under groups
    fireEvent.click(screen.getByTestId('results-cardtab-costs_prices'))
    first.unmount()
    status.current = { ...status.current, objective: 90, solve_time: 2.1 }
    await renderResults()
    expect(screen.getByTestId('summary-stub')).toBeTruthy()
  })

  it('no finished solve leaves the remembered tab alone', async () => {
    status.current = { running: false, status: 'idle', condition: null, objective: null, solve_time: null }
    localStorage.setItem('results:active-tab', 'dispatch')
    await renderResults()
    expect(screen.getByTestId('dispatch-stub')).toBeTruthy()
  })

  it('a tab the assistant asks for wins over the new-solve rule', async () => {
    useUIStore.setState({ resultsTabRequest: 'emissions' })
    await renderResults()
    expect(screen.getByTestId('emissions-stub')).toBeTruthy()
  })

  it('the assistant can ask for the Summary tab by name', async () => {
    localStorage.setItem('results:solve-seen', 'optimal|100|1.5')
    localStorage.setItem('results:active-tab', 'dispatch')
    useUIStore.setState({ resultsTabRequest: 'summary' })
    await renderResults()
    expect(screen.getByTestId('summary-stub')).toBeTruthy()
  })
})


it('a request for the already active tab still wins over a newly observed solve', async () => {
  localStorage.setItem('results:active-tab', 'loadflow')
  useUIStore.setState({ resultsTabRequest: 'loadflow' })
  await renderResults()
  expect(screen.getByTestId('loadflow-stub')).toBeTruthy()
  expect(screen.queryByTestId('summary-stub')).toBeNull()
})
