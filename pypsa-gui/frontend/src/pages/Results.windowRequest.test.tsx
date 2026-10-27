// A checked chart's "Open in Results" brings its window: the Results shell applies the
// one-shot window request to the Horizon filter, over the opening default.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'

const { stub, SNAPSHOTS } = vi.hoisted(() => ({
  stub: (testid: string) => ({ default: () => <div data-testid={testid} /> }),
  // 1000 hourly snapshots: longer than the opening window, so the default narrows.
  SNAPSHOTS: Array.from({ length: 1000 }, (_, i) => new Date(Date.UTC(2026, 0, 1, i)).toISOString().slice(0, 19)),
}))

vi.mock('./results/CapacityExpansion', () => stub('capex-stub'))
vi.mock('./results/Dispatch', () => stub('dispatch-stub'))
vi.mock('./results/LoadFlow', async () => {
  const { useResultsFilter } = await import('./results/filterContext')
  return { default: () => { const f = useResultsFilter(); return <div data-testid="loadflow-window">{f.fromIso}|{f.toIso}</div> } }
})
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
    getSnapshots: vi.fn().mockResolvedValue({ snapshots: SNAPSHOTS, count: SNAPSHOTS.length }),
    getInvestmentPeriods: vi.fn().mockResolvedValue({ periods: [] }),
  },
}))

import Results from './Results'

describe('the checked window request', () => {
  it('opens the Horizon filter on the requested window, once', async () => {
    useUIStore.setState({ uiMode: 'expert', currentProject: 'Demo', compareRailOpen: false, activeSlidePanel: 'results' })
    useUIStore.getState().requestResultsTab('loadflow')
    useUIStore.getState().requestResultsWindow({ start: SNAPSHOTS[10], end: SNAPSHOTS[20] })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><Results /></QueryClientProvider>)
    await act(async () => { await new Promise(r => setTimeout(r, 50)) })
    expect(screen.getByTestId('loadflow-window').textContent).toBe(`${SNAPSHOTS[10]}|${SNAPSHOTS[20]}`.replace(/(\d\d:\d\d):\d\d/g, '$1:00'))
    expect(useUIStore.getState().resultsWindowRequest).toBeNull()
  })

  it('resolves an assistant window by snapshot position, and drops one past the axis', async () => {
    useUIStore.setState({ uiMode: 'expert', currentProject: 'Demo', compareRailOpen: false, activeSlidePanel: 'results' })
    useUIStore.getState().requestResultsTab('loadflow')
    useUIStore.getState().requestResultsWindow({ fromIndex: 10, toIndex: 20 })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const view = render(<QueryClientProvider client={client}><Results /></QueryClientProvider>)
    await act(async () => { await new Promise(r => setTimeout(r, 50)) })
    const shown = `${SNAPSHOTS[10]}|${SNAPSHOTS[20]}`.replace(/(\d\d:\d\d):\d\d/g, '$1:00')
    expect(screen.getByTestId('loadflow-window').textContent).toBe(shown)
    expect(useUIStore.getState().resultsWindowRequest).toBeNull()
    act(() => useUIStore.getState().requestResultsWindow({ fromIndex: 10, toIndex: SNAPSHOTS.length }))
    await act(async () => { await new Promise(r => setTimeout(r, 50)) })
    expect(screen.getByTestId('loadflow-window').textContent).toBe(shown)
    expect(useUIStore.getState().resultsWindowRequest).toBeNull()
    view.unmount()
  })

  async function open(start: string, end: string) {
    useUIStore.setState({ uiMode: 'expert', currentProject: 'Demo', compareRailOpen: false, activeSlidePanel: 'results' })
    useUIStore.getState().requestResultsTab('loadflow')
    useUIStore.getState().requestResultsWindow({ start, end })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><Results /></QueryClientProvider>)
    await act(async () => { await new Promise(r => setTimeout(r, 50)) })
  }

  it('clamps a window that reaches past the horizon and says so', async () => {
    await open('2025-12-31T00:00:00', SNAPSHOTS[20])
    expect(screen.getByTestId('loadflow-window').textContent).toBe(`${SNAPSHOTS[0].slice(0, 16)}:00|${SNAPSHOTS[20].slice(0, 16)}:00`)
    expect(screen.getByTestId('results-window-note').textContent).toMatch(/reaches past this network's horizon/)
  })

  it('tells the user when the window is wholly outside the horizon', async () => {
    await open('2020-01-01T00:00:00', '2020-01-02T00:00:00')
    expect(screen.getByTestId('results-window-note').textContent).toMatch(/outside this network's horizon/)
    expect(useUIStore.getState().resultsWindowRequest).toBeNull()
  })

  it('gives a reversed window its own wording', async () => {
    await open(SNAPSHOTS[20], SNAPSHOTS[10])
    const note = screen.getByTestId('results-window-note').textContent ?? ''
    expect(note).toMatch(/ends before it starts/)
    expect(note).not.toMatch(/outside/)
  })

  it('clears the note when the user edits From or To', async () => {
    await open('2020-01-01T00:00:00', '2020-01-02T00:00:00')
    expect(screen.getByTestId('results-window-note')).toBeTruthy()
    fireEvent.click(screen.getByText('Horizon filter'))
    const from = screen.getByText('From').closest('label')!.querySelector('input')!
    fireEvent.change(from, { target: { value: SNAPSHOTS[5].slice(0, 16) } })
    expect(screen.queryByTestId('results-window-note')).toBeNull()
  })
})
