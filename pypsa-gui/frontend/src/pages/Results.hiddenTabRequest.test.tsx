// Guided-mode spec §10 addendum (gate P23 B3): in Guided an EXPLICIT request
// for a hidden Results tab (asset detail, the assistant's ui_open_panel with
// results_tab, greeting chips) is honoured — the tab renders and the strip
// shows it as one extra, temporary "advanced" tab until the user picks
// another. Only a STORED tab falls back to adequacy. The first describe is the
// QA gate's repro verbatim (qa23/repro/Results.qaHiddenTabRepro.test.tsx).
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'

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

describe('QA repro: Guided entry points that target hidden Results tabs', () => {
  it('Asset detail (Properties/BottomPanel/canvas/assistant) in Guided shows the asset', async () => {
    useUIStore.setState({ uiMode: 'guided', currentProject: 'Demo', compareRailOpen: false })
    useUIStore.getState().requestAssetDetail({ componentClass: 'Generator', name: 'g1' })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><Results /></QueryClientProvider>)
    await act(async () => { await new Promise(r => setTimeout(r, 20)) })
    expect(localStorage.getItem('results:active-tab')).toBe('asset')
    expect(screen.queryByTestId('asset-stub')).toBeTruthy()
  })
  it('assistant results_tab=economics in Guided shows economics', async () => {
    useUIStore.setState({ uiMode: 'guided', currentProject: 'Demo', compareRailOpen: false, activeSlidePanel: 'results' })
    useUIStore.getState().requestResultsTab('economics')
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><Results /></QueryClientProvider>)
    await act(async () => { await new Promise(r => setTimeout(r, 20)) })
    expect(screen.queryByTestId('economics-stub')).toBeTruthy()
  })
})

async function renderResults() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><Results /></QueryClientProvider>)
  await act(async () => { await new Promise(r => setTimeout(r, 20)) })
}
const tabIds = () => Array.from(document.querySelectorAll('[data-testid^="results-tab-"]'))
  .map(el => el.getAttribute('data-testid')!.slice('results-tab-'.length))

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo', compareRailOpen: false, resultsTabRequest: null, activeSlidePanel: 'results' })
})

describe('the temporary advanced tab in Guided', () => {
  it('a requested hidden tab joins the strip, marked advanced, and leaves when another tab is picked', async () => {
    useUIStore.setState({ uiMode: 'guided' })
    useUIStore.getState().requestResultsTab('economics')
    await renderResults()
    expect(tabIds()).toEqual(['economics', 'adequacy', 'fmea'])
    const chip = screen.getByTestId('results-tab-economics')
    expect(chip.getAttribute('data-advanced')).toBe('true')
    expect(chip.textContent).toContain('Advanced')
    expect(chip.className).toContain('border-accent')
    fireEvent.click(screen.getByTestId('results-tab-fmea'))
    expect(tabIds()).toEqual(['adequacy', 'fmea'])
    expect(screen.getByTestId('fmea-stub')).toBeTruthy()
  })

  it('asset detail in Guided shows the Asset Detail chip', async () => {
    useUIStore.setState({ uiMode: 'guided' })
    useUIStore.getState().requestAssetDetail({ componentClass: 'Generator', name: 'g1' })
    await renderResults()
    expect(tabIds()).toEqual(['adequacy', 'fmea', 'asset'])
    expect(screen.getByTestId('results-tab-asset').getAttribute('data-advanced')).toBe('true')
  })

  it('a requested visible tab adds no chip', async () => {
    useUIStore.setState({ uiMode: 'guided' })
    useUIStore.getState().requestResultsTab('fmea')
    await renderResults()
    expect(tabIds()).toEqual(['adequacy', 'fmea'])
    expect(screen.getByTestId('fmea-stub')).toBeTruthy()
  })

  it('only a STORED hidden tab falls back to adequacy — no chip', async () => {
    useUIStore.setState({ uiMode: 'guided' })
    localStorage.setItem('results:active-tab', 'economics')
    await renderResults()
    expect(tabIds()).toEqual(['adequacy', 'fmea'])
    expect(screen.getByTestId('adequacy-stub')).toBeTruthy()
  })

  it('Expert: a requested tab renders with no advanced marker', async () => {
    useUIStore.setState({ uiMode: 'expert' })
    useUIStore.getState().requestResultsTab('economics')
    await renderResults()
    expect(screen.getByTestId('economics-stub')).toBeTruthy()
    expect(document.querySelector('[data-advanced]')).toBeNull()
    expect(screen.queryByText('Advanced')).toBeNull()
  })
})

describe('Guided Results copy', () => {
  it('the subtitle names only the visible tabs', async () => {
    useUIStore.setState({ uiMode: 'guided' })
    await renderResults()
    expect(screen.getByText('Adequacy and failure-mode (FMEA) risk results for the hub design.')).toBeTruthy()
    expect(screen.queryByText(/Capacity expansion, dispatch/)).toBeNull()
  })

  it('Expert keeps its subtitle', async () => {
    useUIStore.setState({ uiMode: 'expert' })
    await renderResults()
    expect(screen.getByText('Capacity expansion, dispatch, load flow, prices, and emissions from the last solve.')).toBeTruthy()
  })
})

// Re-gate N7: picking a tab clears the explicit request, across modes.
describe('picking a tab clears requestedTab (Guided → Expert → Guided)', () => {
  it('after a pick in Expert, returning to Guided shows no advanced chip and falls back', async () => {
    useUIStore.setState({ uiMode: 'guided' })
    useUIStore.getState().requestResultsTab('economics')
    await renderResults()
    expect(screen.getByTestId('results-tab-economics').getAttribute('data-advanced')).toBe('true')
    act(() => { useUIStore.setState({ uiMode: 'expert' }) })
    expect(screen.getByTestId('economics-stub')).toBeTruthy()
    fireEvent.click(screen.getByTestId('results-tab-prices'))
    // Picking the formerly requested tab by hand is a pick, not a request:
    // back in Guided it is a stored hidden tab and falls back.
    fireEvent.click(screen.getByTestId('results-tab-economics'))
    act(() => { useUIStore.setState({ uiMode: 'guided' }) })
    expect(tabIds()).toEqual(['adequacy', 'fmea'])
    expect(document.querySelector('[data-advanced]')).toBeNull()
    expect(screen.getByTestId('adequacy-stub')).toBeTruthy()
    expect(localStorage.getItem('results:active-tab')).toBe('economics')
  })

  it('without a pick the request still stands when Guided returns', async () => {
    useUIStore.setState({ uiMode: 'guided' })
    useUIStore.getState().requestResultsTab('economics')
    await renderResults()
    act(() => { useUIStore.setState({ uiMode: 'expert' }) })
    act(() => { useUIStore.setState({ uiMode: 'guided' }) })
    expect(screen.getByTestId('results-tab-economics').getAttribute('data-advanced')).toBe('true')
    expect(screen.getByTestId('economics-stub')).toBeTruthy()
  })
})


// IC P3 WP3.5: the Investment tab is reachable from a results-tab request
// (the assistant's navigation) — directly in Expert, as a temporary advanced
// tab in Guided.
describe('the Investment tab from a results-tab request', () => {
  it('Expert: the request opens it', async () => {
    useUIStore.setState({ uiMode: 'expert' })
    useUIStore.getState().requestResultsTab('investment')
    await renderResults()
    expect(screen.queryByTestId('investment-stub')).toBeTruthy()
  })

  it('Guided: it joins the strip as an advanced tab', async () => {
    useUIStore.setState({ uiMode: 'guided' })
    useUIStore.getState().requestResultsTab('investment')
    await renderResults()
    expect(screen.queryByTestId('investment-stub')).toBeTruthy()
    expect(screen.getByTestId('results-tab-investment').getAttribute('data-advanced')).toBe('true')
  })
})
