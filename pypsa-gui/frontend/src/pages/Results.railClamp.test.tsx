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

// Drive the splitter through a gesture.
//
// A step is either a `dx` (one mousemove; NEGATIVE moves the cursor left,
// which grows the rail, matching `delta = startX - clientX`) or a thunk run
// mid-gesture with the button still held.
//
// The thunk form is why the round-5 defect survived: every drag assertion in
// this file changed viewport or dock state strictly BEFORE or AFTER a complete
// gesture, so a layout event landing between mousedown and mouseup — which the
// agent does routinely via applyUiNavigate, and OS window-snap does too — was
// unrepresentable. Multi-move and interleaved-layout are the default shapes of
// a real drag, not exotic ones.
type DragStep = number | (() => void)
const DRAG_START_X = 900
function dragSplitter(...steps: DragStep[]) {
  fireEvent.mouseDown(screen.getByTestId('compare-rail-splitter'), { clientX: DRAG_START_X })
  let lastDx = 0
  for (const step of steps) {
    if (typeof step === 'function') act(() => { step() })
    else {
      lastDx = step
      fireEvent.mouseMove(window, { clientX: DRAG_START_X + step, buttons: 1 })
    }
  }
  fireEvent.mouseUp(window, { clientX: DRAG_START_X + lastDx })
}

// Wrapper widths used below, computed from the real constants:
//   SIDEBAR_EXPANDED_W = 240 (layout/Sidebar.tsx), dock open = 380.
//   1440 viewport, dock closed → wrapper 1200 → ceiling 840
//   1440 viewport, dock open   → wrapper 820  → ceiling 460
//   1280 viewport, dock open   → wrapper 660  → ceiling 360 (the floor wins)
const WRAP_1440_UNDOCKED = 1200
const WRAP_1440_DOCKED = 820
const WRAP_1280_DOCKED = 660

/** Open the dock and shrink the wrapper to match, as one layout event. */
function openDockTo(wrapW: number) {
  return () => {
    setViewportWidth(wrapW)
    useUIStore.getState().setAssistantDockOpen(true)
  }
}

beforeEach(() => {
  localStorage.clear()
  setViewportWidth(WRAP_1440_UNDOCKED)
  useUIStore.setState({ currentProject: 'Demo', compareRailOpen: true, assistantDockOpen: false })
  // Through the real setter, so localStorage carries the same value a real
  // drag would have persisted — that is what the reload assertions read.
  useUIStore.getState().setCompareRailWidth(700)
})

afterEach(() => {
  Element.prototype.getBoundingClientRect = originalRect
})

// ── Layout events never write ──────────────────────────────────────────────
describe('compare rail width vs the assistant dock', () => {
  it('renders the desired width when there is room for it', () => {
    renderResults()
    expect(renderedWidth()).toBe(700)
  })

  it('constrains the RENDERED width when the dock takes the space', () => {
    renderResults()
    expect(renderedWidth()).toBe(700)

    act(openDockTo(WRAP_1440_DOCKED))

    expect(renderedWidth()).toBe(460)
  })

  it('preserves the desired width in the store and in localStorage', () => {
    renderResults()

    act(openDockTo(WRAP_1440_DOCKED))

    expect(useUIStore.getState().compareRailWidth).toBe(700)
    expect(localStorage.getItem(WIDTH_KEY)).toBe('700')
  })

  it('gives the width back when the dock closes again', () => {
    renderResults()

    act(openDockTo(WRAP_1440_DOCKED))
    expect(renderedWidth()).toBe(460)

    act(() => {
      setViewportWidth(WRAP_1440_UNDOCKED)
      useUIStore.getState().setAssistantDockOpen(false)
    })

    expect(renderedWidth()).toBe(700)
  })

  it('never renders the rail below its own floor', () => {
    renderResults()

    act(openDockTo(WRAP_1280_DOCKED))

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

// ── A drag records what the user dragged to, and only that ─────────────────
describe('dragging the splitter', () => {
  it('follows the pointer during the drag without persisting anything', () => {
    renderResults()

    fireEvent.mouseDown(screen.getByTestId('compare-rail-splitter'), { clientX: DRAG_START_X })
    fireEvent.mouseMove(window, { clientX: DRAG_START_X + 100, buttons: 1 })

    expect(renderedWidth()).toBe(600)
    expect(useUIStore.getState().compareRailWidth).toBe(700)

    fireEvent.mouseUp(window, { clientX: DRAG_START_X + 100 })
    expect(useUIStore.getState().compareRailWidth).toBe(600)
  })

  it('records nothing when the splitter is clicked without moving', () => {
    // A bare click must not store `startW` — which is the CONSTRAINED width,
    // so on a constrained rail a stray click would overwrite the preference
    // with the ceiling. That is round 2's defect via a different door.
    renderResults()
    act(openDockTo(WRAP_1440_DOCKED))
    expect(renderedWidth()).toBe(460)

    fireEvent.mouseDown(screen.getByTestId('compare-rail-splitter'), { clientX: DRAG_START_X })
    fireEvent.mouseUp(window, { clientX: DRAG_START_X })

    expect(useUIStore.getState().compareRailWidth).toBe(700)
    expect(localStorage.getItem(WIDTH_KEY)).toBe('700')
  })

  it('records a drag that lands where the user released it', () => {
    renderResults()

    dragSplitter(+200)

    expect(useUIStore.getState().compareRailWidth).toBe(500)
    expect(localStorage.getItem(WIDTH_KEY)).toBe('500')
  })

  it('records where the gesture ENDED, not where it passed through', () => {
    renderResults()

    dragSplitter(+300, +100, +200)

    expect(useUIStore.getState().compareRailWidth).toBe(500)
  })

  it('floors a drag past the minimum', () => {
    renderResults()

    dragSplitter(+900)

    expect(useUIStore.getState().compareRailWidth).toBe(RAIL_MIN_W)
  })

  it('stores a width LARGER than fits when dragged left on a constrained rail', () => {
    // The intended consequence of removing the ceiling from the write path.
    // Rendered 460 (ceiling), user pulls left 200 asking for more: we store
    // 660, render 460, and they get 660 back when the dock closes. Clamping
    // this to 460 is exactly what destroyed preferences in earlier rounds.
    renderResults()
    act(openDockTo(WRAP_1440_DOCKED))
    expect(renderedWidth()).toBe(460)

    dragSplitter(-200)

    expect(useUIStore.getState().compareRailWidth).toBe(660)
    expect(renderedWidth()).toBe(460)

    act(() => {
      setViewportWidth(WRAP_1440_UNDOCKED)
      useUIStore.getState().setAssistantDockOpen(false)
    })
    expect(renderedWidth()).toBe(660)
  })

  // ── Layout events landing INSIDE a gesture ───────────────────────────────
  it('records what the user dragged to when the dock opens mid-gesture', () => {
    // applyUiNavigate calls setAssistantDockOpen(true) on an agent turn, with
    // no idea a mouse button is held. The wrapper measured at mousedown (1200)
    // is stale by the time of release (820). Nothing in the write path reads
    // it, so the recorded width is simply where the pointer went.
    renderResults()

    dragSplitter(-200, openDockTo(WRAP_1440_DOCKED))

    expect(useUIStore.getState().compareRailWidth).toBe(900)
    // And the render immediately reflects the NEW constraint, not the old one.
    expect(renderedWidth()).toBe(460)
  })

  it('keeps the preview inside the container when the dock opens mid-gesture', () => {
    // Cosmetic half of the same bug: the in-flight preview used to be
    // constrained by the mousedown ceiling, so it kept painting 840 over an
    // 820px container until release.
    renderResults()

    fireEvent.mouseDown(screen.getByTestId('compare-rail-splitter'), { clientX: DRAG_START_X })
    fireEvent.mouseMove(window, { clientX: DRAG_START_X - 200, buttons: 1 })
    // Desired 900, but the render constrains the preview exactly like it
    // constrains the resting width: ceiling(1200) = 840. The handle stops
    // following once there is no room, which is correct and is the same rule
    // at rest and in flight.
    expect(renderedWidth()).toBe(840)

    act(openDockTo(WRAP_1440_DOCKED))

    expect(renderedWidth()).toBe(460)

    fireEvent.mouseUp(window, { clientX: DRAG_START_X - 200 })
    expect(useUIStore.getState().compareRailWidth).toBe(900)
  })

  it('finishes a gesture whose mouseup was lost, instead of freezing the preview', () => {
    // Released outside the window: `onUp` never fires. The next move over the
    // document arrives with no button held, which is the only signal we get.
    // Without acting on it the preview stays frozen over a store that never
    // agreed to it — through dock toggles and resizes — and the listeners
    // stay live.
    renderResults()
    const splitter = screen.getByTestId('compare-rail-splitter')

    fireEvent.mouseDown(splitter, { clientX: DRAG_START_X })
    fireEvent.mouseMove(window, { clientX: DRAG_START_X - 200, buttons: 1 })
    expect(renderedWidth()).toBe(840)
    expect(useUIStore.getState().compareRailWidth).toBe(700)

    // Button released off-window; the pointer comes back over the document.
    fireEvent.mouseMove(window, { clientX: DRAG_START_X - 200, buttons: 0 })

    // The gesture is honoured at the last position we actually observed...
    expect(useUIStore.getState().compareRailWidth).toBe(900)
    // ...and the render now agrees with the store rather than with a stale
    // preview. This is the assertion that was missing.
    expect(renderedWidth()).toBe(840)

    // And it really is over: a later layout event is driven by the store.
    act(openDockTo(WRAP_1440_DOCKED))
    expect(renderedWidth()).toBe(460)
  })

  it('does not let an abandoned gesture write across the next one', () => {
    renderResults()
    const splitter = screen.getByTestId('compare-rail-splitter')

    fireEvent.mouseDown(splitter, { clientX: DRAG_START_X })
    fireEvent.mouseMove(window, { clientX: DRAG_START_X - 200, buttons: 1 })

    act(openDockTo(WRAP_1440_DOCKED))

    dragSplitter(+100)

    // Only the second gesture's release counts: startW 460, dragged right 100.
    expect(useUIStore.getState().compareRailWidth).toBe(360)
  })

  it('does not write the constraint on a narrow wrapper', () => {
    // Wrapper 660: the ceiling IS the floor, so the rail cannot move. A drag
    // still records where the pointer went; what it must never record is 360
    // simply because that is all the room there is.
    renderResults()
    act(openDockTo(WRAP_1280_DOCKED))
    expect(renderedWidth()).toBe(RAIL_MIN_W)

    dragSplitter(-300)

    expect(useUIStore.getState().compareRailWidth).toBe(660)
    expect(renderedWidth()).toBe(RAIL_MIN_W)
  })
})
