import { describe, it, expect, beforeEach, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'

// The comparison rail's clamp has to re-run when the AVAILABLE width changes,
// not only when the rail itself opens.
//
// Opening the assistant dock takes a fixed 380px out of App.tsx's body row,
// which is the same row Results lays out in. With the rail already open at its
// persisted width, nothing re-measured: on a 1280px screen with the rail at
// its stored 560, main drops to ~660, the rail stays 560, and the live Results
// pane collapses to ~99px — unrecoverable short of dragging the splitter.
//
// What these tests can and cannot show: jsdom does no layout, and
// vitest.setup.ts pins every getBoundingClientRect to a fixed 500px box, so
// the wrapper does NOT actually shrink here when the dock opens. They
// therefore pin the defect that is real code — that the clamp RE-EVALUATES on
// a dock toggle instead of going stale — by putting the rail in the
// already-too-wide state the shrink would produce and checking the toggle
// notices. The pixel outcome needs the built app.

// vi.hoisted, not a plain const: Results.tsx's own imports run these factories
// during the import phase, before this file's body executes, so a top-level
// `const stub` would still be in its temporal dead zone. The returned
// component closure runs at render time, when the jsx runtime is ready.
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
    getSnapshots: vi.fn().mockResolvedValue({ snapshots: [], count: 0 }),
    getInvestmentPeriods: vi.fn().mockResolvedValue({ periods: [] }),
  },
}))

import Results from './Results'

// vitest.setup.ts's global getBoundingClientRect stub reports 500px wide for
// every element, so the wrapper the clamp measures is 500 and RAIL_MIN_W is
// 360 — anything above 140 no longer fits.
const WRAP_W = 500
const RAIL_MIN_W = 360
const MAX_FITTING = WRAP_W - RAIL_MIN_W // 140

function renderResults() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <Results />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  localStorage.clear()
  useUIStore.setState({
    currentProject: 'Demo',
    compareRailOpen: true,
    // Fits inside MAX_FITTING, so the mount-time clamp is a no-op and any
    // later change is unambiguously attributable to the toggle under test.
    compareRailWidth: 120,
    assistantDockOpen: false,
  })
})

describe('compare rail clamp vs the assistant dock', () => {
  it('re-clamps a rail that no longer fits when the dock opens', () => {
    renderResults()
    // Mount clamp left the fitting width alone.
    expect(useUIStore.getState().compareRailWidth).toBe(120)

    // Stand in for the wrapper shrinking under a rail that used to fit —
    // set directly rather than through setCompareRailWidth, whose own floor
    // would rewrite the value before the effect ever saw it.
    act(() => { useUIStore.setState({ compareRailWidth: 560 }) })
    // Nothing re-measures on a width change alone, which is the point: this
    // is the stale state the user was left in.
    expect(useUIStore.getState().compareRailWidth).toBe(560)

    act(() => { useUIStore.getState().setAssistantDockOpen(true) })

    expect(useUIStore.getState().compareRailWidth).toBe(Math.max(RAIL_MIN_W, MAX_FITTING))
  })

  it('only ever shrinks — it does not grow the rail back when room reappears', () => {
    renderResults()
    act(() => { useUIStore.setState({ compareRailWidth: 560 }) })
    act(() => { useUIStore.getState().setAssistantDockOpen(true) })
    expect(useUIStore.getState().compareRailWidth).toBe(RAIL_MIN_W)

    // Closing the dock gives the width back. The rail must NOT spring back to
    // 560: compareRailWidth is persisted and is usually a width the user chose
    // by dragging, so restoring it automatically would silently overwrite a
    // preference rather than fix a layout.
    act(() => { useUIStore.getState().setAssistantDockOpen(false) })

    expect(useUIStore.getState().compareRailWidth).toBe(RAIL_MIN_W)
  })

  it('leaves a rail that still fits untouched across a dock toggle', () => {
    // The negative control: a clamp that fired unconditionally would pass both
    // tests above while resizing rails that were perfectly fine.
    renderResults()

    act(() => { useUIStore.getState().setAssistantDockOpen(true) })

    expect(useUIStore.getState().compareRailWidth).toBe(120)
  })
})
