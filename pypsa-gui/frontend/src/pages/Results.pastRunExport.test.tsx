import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'

// LC-03 past-run parity (UX ask 1): the discussed-run row offers the one
// Export chooser, on the results workbook, in Guided and in Expert.

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
vi.mock('./results/InvestmentTab', () => stub('investment-stub'))
// The rail's CompareView reports the tab Results seeded it with.
vi.mock('./CompareView', () => ({
  default: ({ initialTab }: { initialTab?: string }) => <div data-testid="compare-stub" data-initial-tab={initialTab} />,
}))

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

async function renderResults() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><Results /></QueryClientProvider>)
  await act(async () => { await new Promise(r => setTimeout(r, 20)) })
}

afterEach(() => {
  useUIStore.setState({ discussedRun: null, ioModalRequest: null, ioModalFormat: null, uiMode: 'expert' })
})

describe('the discussed-run row', () => {
  for (const mode of ['guided', 'expert'] as const) {
    it(`opens the Export chooser on the results workbook (${mode})`, async () => {
      useUIStore.setState({
        uiMode: mode, currentProject: 'oj-grid', activeSlidePanel: 'results',
        discussedRun: { runId: 'run-past', section: 'results_capacity' },
        ioModalRequest: null, ioModalFormat: null,
      })
      await renderResults()
      const row = screen.getByTestId('discussed-run-chrome')
      const link = screen.getByTestId('discussed-run-export')
      expect(row.contains(link)).toBe(true)
      expect(link.textContent).toContain('Export…')
      fireEvent.click(link)
      const s = useUIStore.getState()
      expect(s.ioModalRequest).toBe('export')
      expect(s.ioModalFormat).toBe('workbook')
      // Results and the past run stay on screen.
      expect(s.activeSlidePanel).toBe('results')
      expect(s.discussedRun?.runId).toBe('run-past')
    })
  }

  it('is absent without a discussed run', async () => {
    useUIStore.setState({ uiMode: 'expert', currentProject: 'oj-grid', discussedRun: null })
    await renderResults()
    expect(screen.queryByTestId('discussed-run-export')).toBeNull()
  })
})
