import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
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

// The resize listener coalesces to one measurement per animation frame, so a
// dispatched resize is not observable until a frame has run.
async function flushFrame() {
  await act(async () => {
    await new Promise<void>((resolve) => { requestAnimationFrame(() => resolve()) })
  })
}

// Drive the splitter. The handle is on the rail's LEFT edge, so a NEGATIVE
// dx (cursor moves left) grows the rail — matching `delta = startX - clientX`.
function dragSplitter(dx: number) {
  const startX = 900
  fireEvent.mouseDown(screen.getByTestId('compare-rail-splitter'), { clientX: startX })
  fireEvent.mouseMove(window, { clientX: startX + dx })
  fireEvent.mouseUp(window)
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

  it('recomputes on a window resize without touching the stored width', async () => {
    renderResults()
    expect(renderedWidth()).toBe(700)

    setViewportWidth(900)
    act(() => { window.dispatchEvent(new Event('resize')) })
    await flushFrame()

    expect(renderedWidth()).toBe(540)
    expect(useUIStore.getState().compareRailWidth).toBe(700)
    expect(localStorage.getItem(WIDTH_KEY)).toBe('700')
  })
})

// ── Dragging a CONSTRAINED rail must not record the constraint ─────────────
//
// The desired/rendered split stops a layout event from destroying the user's
// width. These pin the gesture that could still do it: a drag whose result is
// decided by the ceiling rather than by where the user let go.
describe('dragging the splitter while the rail is constrained', () => {
  it('records a drag that lands short of the ceiling', () => {
    // The control. With room to spare (wrapper 1440 → ceiling 1080), dragging
    // right to shrink is an ordinary choice and must be saved normally —
    // including to localStorage, so it survives a reload.
    renderResults()
    expect(renderedWidth()).toBe(700)

    dragSplitter(+200)

    expect(useUIStore.getState().compareRailWidth).toBe(500)
    expect(localStorage.getItem(WIDTH_KEY)).toBe('500')
  })

  it('does not shrink the stored width when the drag is pinned at the ceiling', () => {
    // Reviewer's case (a). Desired 700, wrapper 860, ceiling 500, rendered
    // 500. The user drags LEFT to make the rail bigger — the natural response
    // to it having just shrunk — and there is no room, so every position pins
    // at 500. Writing that would replace their 700 with this window's limit.
    renderResults()
    act(() => {
      setViewportWidth(860)
      useUIStore.getState().setAssistantDockOpen(true)
    })
    expect(renderedWidth()).toBe(500)

    dragSplitter(-200)

    expect(useUIStore.getState().compareRailWidth).toBe(700)
    expect(localStorage.getItem(WIDTH_KEY)).toBe('700')
  })

  it('does not write the floor when the wrapper is too narrow to honour any drag', () => {
    // Reviewer's case (b), and the worse one: it needs no pinning gesture.
    // 1280px with the dock open and the sidebar expanded leaves a 680px
    // wrapper, under 2 × RAIL_MIN_W — so the ceiling IS the floor and every
    // mouse position resolves to 360. The first pixel of any drag, in either
    // direction, used to write 360 and destroy the stored 700.
    renderResults()
    act(() => {
      setViewportWidth(680)
      useUIStore.getState().setAssistantDockOpen(true)
    })
    expect(renderedWidth()).toBe(RAIL_MIN_W)

    dragSplitter(-50)
    expect(useUIStore.getState().compareRailWidth).toBe(700)

    dragSplitter(+50)
    expect(useUIStore.getState().compareRailWidth).toBe(700)
    expect(localStorage.getItem(WIDTH_KEY)).toBe('700')
  })

  it('still records growing INTO the ceiling from a smaller stored width', () => {
    // The guard must not swallow a real choice. Stored 400 with a 860 wrapper
    // (ceiling 500): dragging left to 500 or beyond is the user genuinely
    // asking for the widest rail that fits, and must be saved.
    renderResults()
    act(() => {
      setViewportWidth(860)
      useUIStore.getState().setAssistantDockOpen(true)
      useUIStore.getState().setCompareRailWidth(400)
    })
    expect(renderedWidth()).toBe(400)

    dragSplitter(-300)

    expect(useUIStore.getState().compareRailWidth).toBe(500)
    expect(localStorage.getItem(WIDTH_KEY)).toBe('500')
  })
})
