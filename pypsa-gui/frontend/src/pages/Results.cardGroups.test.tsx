// W10 R1 (card-workspaces.md §8): the results grouped by card. A card outside
// the scope stays in the strip, greyed, and opens to ScopeGate's "Outside
// scope" with the earlier results kept below, dimmed and read-only (RC3 A);
// each group header says how fresh its results are and keeps a slot for H2b;
// Asset Detail is a drawer over the tab beneath it.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import type { FlowScopeRegistry } from '../api/flow'

const { stub, status, getFlowScope } = vi.hoisted(() => ({
  stub: (testid: string) => ({ default: () => <div data-testid={testid} /> }),
  status: { current: {} as Record<string, unknown> },
  getFlowScope: vi.fn(),
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
vi.mock('./results/EnergyFlowsTab', () => stub('flows-stub'))
vi.mock('./results/LoadShapeTab', () => stub('loadshape-stub'))
vi.mock('./results/asset/AssetDetail', () => stub('asset-stub'))
vi.mock('./results/InvestmentTab', () => stub('investment-stub'))
vi.mock('./results/SummaryTab', () => stub('summary-stub'))
vi.mock('./CompareView', () => ({ default: () => <div data-testid="compare-stub" /> }))
vi.mock('../api/simulation', () => ({
  simulationApi: { getStatus: () => Promise.resolve(status.current) },
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
vi.mock('../api/flow', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/flow')>()
  return { ...actual, getFlowScope: (...a: unknown[]) => getFlowScope(...a) }
})

import Results from './Results'

const opt = (id: string) => ({ id, label: id, status: 'available' as const, reason: null })
function registry(groups: string[]): FlowScopeRegistry {
  const ids = ['capacity', 'grid', 'control', 'reliability', 'commercial', 'handoff']
  return {
    cards: ids.map(id => ({ id, label: id, options: [opt('a')], locked_reason: null, intended: [], sections: [] })),
    scope: { version: 1, groups, options: Object.fromEntries(ids.map(id => [id, groups.includes(id) ? ['a'] : []])),
      source: 'project', preset_id: 'p' },
    preset: { version: 1, groups, options: {} },
  } as unknown as FlowScopeRegistry
}

async function renderResults() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><Results /></QueryClientProvider>)
  await act(async () => { await new Promise(r => setTimeout(r, 30)) })
}

beforeEach(() => {
  localStorage.clear()
  status.current = { running: false, status: 'done', condition: 'optimal', objective: 5, solve_time: 1, dispatch: 'fresh' }
  getFlowScope.mockResolvedValue(registry(['capacity', 'grid', 'reliability', 'commercial', 'handoff']))
  useUIStore.setState({ uiMode: 'expert', currentProject: 'Demo', compareRailOpen: false,
    resultsTabRequest: null, activeSlidePanel: 'results' })
})

describe('the group strip', () => {
  it('lists Summary and the six cards, in the registry order', async () => {
    await renderResults()
    const ids = Array.from(document.querySelectorAll('[data-testid^="results-group-"]'))
      .map(el => el.getAttribute('data-testid')!.slice('results-group-'.length))
    expect(ids).toEqual(['summary', 'capacity', 'grid', 'control', 'reliability', 'commercial', 'handoff'])
  })

  it('an out-of-scope card stays in the strip, greyed, and opens to "Outside scope"', async () => {
    await renderResults()
    const control = screen.getByTestId('results-group-control')
    expect(control.getAttribute('data-outside-scope')).toBe('true')
    expect(screen.getByTestId('results-group-grid').getAttribute('data-outside-scope')).toBeNull()
    fireEvent.click(control)
    expect(screen.getByTestId('results-outside-scope').textContent).toContain('Outside scope.')
    expect(screen.getByTestId('results-outside-scope-add')).toBeTruthy()
    // No tab yet: no earlier results to keep.
    expect(screen.queryByTestId('results-earlier')).toBeNull()
  })

  it('earlier results of a switched-off card stay below, dimmed and read-only', async () => {
    getFlowScope.mockResolvedValue(registry(['capacity', 'commercial']))
    localStorage.setItem('results:active-tab', 'loadflow')
    await renderResults()
    fireEvent.click(screen.getByTestId('results-group-grid'))
    expect(screen.getByTestId('results-outside-scope')).toBeTruthy()
    const earlier = screen.getByTestId('results-earlier')
    expect(earlier.textContent).toContain('Read-only while AC power flow & grid is outside the scope')
    const body = within(earlier).getByTestId('loadflow-stub').closest('[inert]')
    expect(body).toBeTruthy()
    expect(body!.className).toContain('opacity-50')
  })

  it('a card in scope with no tab yet says so; a card with tabs shows its card tabs', async () => {
    getFlowScope.mockResolvedValue(registry(['capacity', 'control', 'reliability']))
    await renderResults()
    fireEvent.click(screen.getByTestId('results-group-control'))
    expect(screen.getByTestId('results-gempty').textContent).toContain('Grid study ›')
    fireEvent.click(screen.getByTestId('results-gempty-open'))
    expect(useUIStore.getState().activeSlidePanel).toBe('campusStudy')
    fireEvent.click(screen.getByTestId('results-group-control'))
    fireEvent.click(screen.getByTestId('results-group-capacity'))
    const tabs = Array.from(document.querySelectorAll('[data-testid^="results-cardtab-"]'))
      .map(el => el.textContent)
    expect(tabs).toEqual(['Build', 'Operation', 'Curtailment', 'Costs & prices'])
    fireEvent.click(screen.getByTestId('results-cardtab-operation'))
    expect(screen.getByTestId('results-subviews').textContent).toContain('Dispatch')
  })
})

describe('the empty groups', () => {
  it('Hand-off opens Reports in Expert and its section in Guided, through the shared navigation', async () => {
    await renderResults()
    fireEvent.click(screen.getByTestId('results-group-handoff'))
    expect(screen.getByTestId('results-gempty').textContent).toContain('Open Reports ›')
    fireEvent.click(screen.getByTestId('results-gempty-open'))
    expect(useUIStore.getState().activeSlidePanel).toBe('reports')
    expect(useUIStore.getState().activeSection).toBe('results')
    useUIStore.setState({ uiMode: 'guided' })
    fireEvent.click(screen.getByTestId('results-group-handoff'))
    fireEvent.click(screen.getByTestId('results-gempty-open'))
    expect(useUIStore.getState().activeSlidePanel).toBe('hubDesign')
  })
})

describe('the group header', () => {
  it('shows freshness, the "Out of date" pill when stale, and an empty slot for H2b', async () => {
    await renderResults()
    fireEvent.click(screen.getByTestId('results-group-capacity'))
    expect(screen.getByTestId('results-ghead-freshness').textContent).toContain('From the latest run')
    expect(screen.getByTestId('results-ghead-slot').children.length).toBe(0)
    status.current = { ...status.current, dispatch: 'stale' }
    await renderResults()
    fireEvent.click(screen.getAllByTestId('results-group-capacity')[1])
    expect(screen.getAllByTestId('results-ghead-freshness')[1].textContent).toContain('Out of date')
  })

  it('names a failed run in the server’s words', async () => {
    status.current = { running: false, status: 'failed', condition: null, objective: null, solve_time: null,
      failure: { category: 'infeasible', title: 'The model is infeasible', hint: 'Check the demand.', detail: '' } }
    await renderResults()
    fireEvent.click(screen.getByTestId('results-group-capacity'))
    expect(screen.getByTestId('results-ghead-failed').textContent)
      .toContain('Did not complete: The model is infeasible. Check the demand.')
    fireEvent.click(screen.getByTestId('results-ghead-failed-open'))
    expect(useUIStore.getState().activeSection).toBe('readiness')
  })
})

describe('Asset Detail', () => {
  it('opens as a drawer over the tab beneath it and closes back to it', async () => {
    await renderResults()
    fireEvent.click(screen.getByTestId('results-group-capacity'))
    fireEvent.click(screen.getByTestId('results-cardtab-operation'))
    fireEvent.click(screen.getByTestId('results-tab-asset'))
    expect(screen.getByTestId('asset-drawer')).toBeTruthy()
    expect(screen.getByTestId('asset-stub')).toBeTruthy()
    expect(screen.getByTestId('dispatch-stub')).toBeTruthy()   // still mounted beneath
    fireEvent.click(screen.getByTestId('asset-drawer-close'))
    expect(screen.queryByTestId('asset-drawer')).toBeNull()
    expect(screen.getByTestId('dispatch-stub')).toBeTruthy()
  })
})
