import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'

// The comparison rail's width has two meanings that must not be conflated.
//
//   DESIRED  — `compareRailWidth` in the store. What the user dragged the rail
//              to. Persisted to localStorage. Written by the drag and nothing
//              else.
//   RENDERED — `min(desired, wrapW - RAIL_MIN_W)`, floored at RAIL_MIN_W.
//              Recomputed from a measurement on every layout change. Never
//              stored.
//
// The version these tests replaced clamped by writing the smaller value back
// through `setCompareRailWidth`, which persists. A user who dragged the rail to
// 700 on a 1440px laptop and opened the assistant once had their 700 rewritten
// to 500 in the store AND localStorage; closing the dock did not restore it and
// neither did a reload. It "only ever shrank", which is a clamp by the letter
// while being exactly the silent data loss a clamp is meant to prevent.
//
// Unlike the previous round, these tests can see real widths: they replace
// vitest.setup.ts's fixed-500px getBoundingClientRect for the duration of the
// file, which lets them model the reported 1440 → dock opens → 860 sequence
// directly rather than standing in for it.

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

const RAIL_MIN_W = 360
const WIDTH_KEY = 'network-diagram:compare-rail-width'

// vitest.setup.ts pins getBoundingClientRect to a fixed 500px box for every
// element, which is narrower than 2 × RAIL_MIN_W and so degenerate for this
// component. Swap in a controllable width for this file only.
const originalRect = Element.prototype.getBoundingClientRect
function setViewportWidth(width: number) {
  Element.prototype.getBoundingClientRect = () => ({
    width, height: 500, top: 0, left: 0, right: width, bottom: 500, x: 0, y: 0,
    toJSON() {},
  }) as DOMRect
}

function renderResults() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <Results />
    </QueryClientProvider>,
  )
}

function renderedWidth(): number {
  return Number.parseFloat(screen.getByTestId('compare-rail').style.width)
}

beforeEach(() => {
  localStorage.clear()
  setViewportWidth(1440)
  useUIStore.setState({ currentProject: 'Demo', compareRailOpen: true, assistantDockOpen: false })
  // Through the real setter, so localStorage carries the same value a real
  // drag would have persisted — that is what the reload assertions read.
  useUIStore.getState().setCompareRailWidth(700)
})

afterEach(() => {
  Element.prototype.getBoundingClientRect = originalRect
})

describe('compare rail width vs the assistant dock', () => {
  it('renders the desired width when there is room for it', () => {
    renderResults()
    expect(renderedWidth()).toBe(700)
  })

  it('constrains the RENDERED width when the dock takes the space', () => {
    renderResults()
    expect(renderedWidth()).toBe(700)

    // The dock opens: 1440 − 380 (dock) − ~200 (sidebar etc.) ≈ 860 for this
    // component's wrapper. max = 860 − 360 = 500.
    act(() => {
      setViewportWidth(860)
      useUIStore.getState().setAssistantDockOpen(true)
    })

    expect(renderedWidth()).toBe(500)
  })

  it('preserves the desired width in the store and in localStorage', () => {
    renderResults()

    act(() => {
      setViewportWidth(860)
      useUIStore.getState().setAssistantDockOpen(true)
    })

    // The rail on screen shrank to 500 (asserted above), but the user's 700
    // is untouched — this is the assertion the previous implementation failed.
    expect(useUIStore.getState().compareRailWidth).toBe(700)
    // And a reload would restore it, because localStorage was never rewritten.
    expect(localStorage.getItem(WIDTH_KEY)).toBe('700')
  })

  it('gives the width back when the dock closes again', () => {
    renderResults()

    act(() => {
      setViewportWidth(860)
      useUIStore.getState().setAssistantDockOpen(true)
    })
    expect(renderedWidth()).toBe(500)

    act(() => {
      setViewportWidth(1440)
      useUIStore.getState().setAssistantDockOpen(false)
    })

    expect(renderedWidth()).toBe(700)
  })

  it('never renders the rail below its own floor', () => {
    // A window so narrow that `wrapW - RAIL_MIN_W` drops under RAIL_MIN_W.
    // The rail must not be squeezed past its minimum — at that point both
    // panes simply overflow, which is the pre-existing behaviour.
    renderResults()

    act(() => {
      setViewportWidth(500)
      useUIStore.getState().setAssistantDockOpen(true)
    })

    expect(renderedWidth()).toBe(RAIL_MIN_W)
    expect(useUIStore.getState().compareRailWidth).toBe(700)
  })

  it('recomputes on a window resize without touching the stored width', () => {
    renderResults()
    expect(renderedWidth()).toBe(700)

    act(() => {
      setViewportWidth(900)
      window.dispatchEvent(new Event('resize'))
    })

    expect(renderedWidth()).toBe(540)
    expect(useUIStore.getState().compareRailWidth).toBe(700)
    expect(localStorage.getItem(WIDTH_KEY)).toBe('700')
  })
})
