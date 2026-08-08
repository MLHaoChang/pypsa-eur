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

// Drive the splitter through a gesture: mousedown, one mousemove per `dx`,
// then mouseup. The handle is on the rail's LEFT edge, so a NEGATIVE dx
// (cursor moves left) grows the rail — matching `delta = startX - clientX`.
//
// Multi-move is the whole point. A single-move drag cannot express the defect
// this suite exists for: the ratchet needed one intermediate write to disarm
// the guard before a later move landed on the ceiling. Every real drag is
// dozens of moves.
const DRAG_START_X = 900
function dragSplitter(...dxs: number[]) {
  fireEvent.mouseDown(screen.getByTestId('compare-rail-splitter'), { clientX: DRAG_START_X })
  for (const dx of dxs) fireEvent.mouseMove(window, { clientX: DRAG_START_X + dx })
  fireEvent.mouseUp(window, { clientX: DRAG_START_X + (dxs[dxs.length - 1] ?? 0) })
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

// Wrapper widths used below, computed from the real constants:
//   SIDEBAR_EXPANDED_W = 240 (layout/Sidebar.tsx), dock open = 380.
//   1440 viewport → wrapper 820 → ceiling 460
//   1280 viewport → wrapper 660 → ceiling 360 (the floor wins)
const WRAP_1440_DOCKED = 820
const WRAP_1280_DOCKED = 660

describe('compare rail width vs the assistant dock', () => {
  it('renders the desired width when there is room for it', () => {
    renderResults()
    expect(renderedWidth()).toBe(700)
  })

  it('constrains the RENDERED width when the dock takes the space', () => {
    renderResults()
    expect(renderedWidth()).toBe(700)

    act(() => {
      setViewportWidth(WRAP_1440_DOCKED)
      useUIStore.getState().setAssistantDockOpen(true)
    })

    expect(renderedWidth()).toBe(460)
  })

  it('preserves the desired width in the store and in localStorage', () => {
    renderResults()

    act(() => {
      setViewportWidth(WRAP_1440_DOCKED)
      useUIStore.getState().setAssistantDockOpen(true)
    })

    // The rail on screen shrank to 460, but the user's 700 is untouched.
    expect(useUIStore.getState().compareRailWidth).toBe(700)
    // And a reload would restore it, because localStorage was never rewritten.
    expect(localStorage.getItem(WIDTH_KEY)).toBe('700')
  })

  it('gives the width back when the dock closes again', () => {
    renderResults()

    act(() => {
      setViewportWidth(WRAP_1440_DOCKED)
      useUIStore.getState().setAssistantDockOpen(true)
    })
    expect(renderedWidth()).toBe(460)

    act(() => {
      setViewportWidth(1440)
      useUIStore.getState().setAssistantDockOpen(false)
    })

    expect(renderedWidth()).toBe(700)
  })

  it('never renders the rail below its own floor', () => {
    renderResults()

    act(() => {
      setViewportWidth(WRAP_1280_DOCKED)
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

// ── Dragging must record the gesture, never the constraint ─────────────────
//
// The desired/rendered split stops a LAYOUT event destroying the saved width.
// These pin the gestures that could still do it.
describe('dragging the splitter', () => {
  it('follows the pointer during the drag without persisting anything', () => {
    renderResults()

    fireEvent.mouseDown(screen.getByTestId('compare-rail-splitter'), { clientX: DRAG_START_X })
    fireEvent.mouseMove(window, { clientX: DRAG_START_X + 100 })

    // The rail tracks the cursor...
    expect(renderedWidth()).toBe(600)
    // ...but nothing is written until release. Deferring the decision must not
    // cost the live feedback.
    expect(useUIStore.getState().compareRailWidth).toBe(700)

    fireEvent.mouseUp(window, { clientX: DRAG_START_X + 100 })
    expect(useUIStore.getState().compareRailWidth).toBe(600)
  })

  it('records a drag that lands short of the ceiling', () => {
    renderResults()
    expect(renderedWidth()).toBe(700)

    dragSplitter(+200)

    expect(useUIStore.getState().compareRailWidth).toBe(500)
    expect(localStorage.getItem(WIDTH_KEY)).toBe('500')
  })

  it('records where the gesture ENDED, not where it passed through', () => {
    renderResults()

    dragSplitter(+300, +100, +200)

    expect(useUIStore.getState().compareRailWidth).toBe(500)
  })

  it('does not shrink the stored width when the drag is pinned at the ceiling', () => {
    // Desired 700, wrapper 820, ceiling 460, rendered 460. The user drags LEFT
    // to make the rail bigger — the natural response to it having shrunk — and
    // there is no room, so the release pins at 460.
    renderResults()
    act(() => {
      setViewportWidth(WRAP_1440_DOCKED)
      useUIStore.getState().setAssistantDockOpen(true)
    })
    expect(renderedWidth()).toBe(460)

    dragSplitter(-200)

    expect(useUIStore.getState().compareRailWidth).toBe(700)
    expect(localStorage.getItem(WIDTH_KEY)).toBe('700')
  })

  // ── The ratchet ────────────────────────────────────────────────────────
  //
  // These two are why the decision moved to the gesture. The previous guard
  // compared against the LIVE stored width, which the same drag was rewriting,
  // so a single sub-ceiling write disarmed it for every later move.
  it('does not ratchet the stored width down across a multi-move drag', () => {
    renderResults()
    act(() => {
      setViewportWidth(WRAP_1440_DOCKED)
      useUIStore.getState().setAssistantDockOpen(true)
    })
    expect(renderedWidth()).toBe(460)

    // Pull left to widen, drift one pixel right, pull left again. The rail
    // renders 460 at both ends: a visually identical, no-op gesture.
    dragSplitter(-200, +1, -200)

    expect(useUIStore.getState().compareRailWidth).toBe(700)
    expect(localStorage.getItem(WIDTH_KEY)).toBe('700')
    expect(renderedWidth()).toBe(460)
  })

  it('does not record a one-pixel press drift that precedes a pinned release', () => {
    renderResults()
    act(() => {
      setViewportWidth(WRAP_1440_DOCKED)
      useUIStore.getState().setAssistantDockOpen(true)
    })

    dragSplitter(+1, -200)

    expect(useUIStore.getState().compareRailWidth).toBe(700)
    expect(localStorage.getItem(WIDTH_KEY)).toBe('700')
  })

  it('does not write the floor when the wrapper is too narrow to honour any drag', () => {
    // Wrapper 660, under 2 × RAIL_MIN_W, so the ceiling IS the floor and every
    // position resolves to 360. No gesture here expresses a choice.
    renderResults()
    act(() => {
      setViewportWidth(WRAP_1280_DOCKED)
      useUIStore.getState().setAssistantDockOpen(true)
    })
    expect(renderedWidth()).toBe(RAIL_MIN_W)

    dragSplitter(-50, +50, -50)
    expect(useUIStore.getState().compareRailWidth).toBe(700)

    dragSplitter(+50)
    expect(useUIStore.getState().compareRailWidth).toBe(700)
    expect(localStorage.getItem(WIDTH_KEY)).toBe('700')
  })

  it('still records growing INTO the ceiling from a smaller stored width', () => {
    // Stored 400, wrapper 820, ceiling 460. Dragging left past the ceiling is
    // the user genuinely asking for the widest rail that fits.
    renderResults()
    act(() => {
      setViewportWidth(WRAP_1440_DOCKED)
      useUIStore.getState().setAssistantDockOpen(true)
      useUIStore.getState().setCompareRailWidth(400)
    })
    expect(renderedWidth()).toBe(400)

    dragSplitter(-300)

    expect(useUIStore.getState().compareRailWidth).toBe(460)
    expect(localStorage.getItem(WIDTH_KEY)).toBe('460')
  })

  it('discards a gesture whose mouseup was lost instead of letting it outlive the next one', () => {
    // Released outside the window: onUp never fires, so the listeners stay
    // attached holding a stale wrapper width. Without a teardown at mousedown,
    // the next gesture runs two handlers and the stale one's decision — made
    // against the OLD, wider wrapper — could still be written.
    renderResults()
    const splitter = screen.getByTestId('compare-rail-splitter')

    fireEvent.mouseDown(splitter, { clientX: DRAG_START_X })
    fireEvent.mouseMove(window, { clientX: DRAG_START_X - 200 })
    // No mouseup. The dock opens and a fresh gesture begins.

    act(() => {
      setViewportWidth(WRAP_1440_DOCKED)
      useUIStore.getState().setAssistantDockOpen(true)
    })

    dragSplitter(-200)

    // Only the second gesture decides, and it is pinned, so nothing is written.
    expect(useUIStore.getState().compareRailWidth).toBe(700)
    expect(localStorage.getItem(WIDTH_KEY)).toBe('700')
  })
})
