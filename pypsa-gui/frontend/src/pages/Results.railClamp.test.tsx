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
import { RAIL_MIN_W } from './results/railWidth'
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

// Drive the splitter.
//
// EVERY simulated drag must go through `beginDrag`/`dragSplitter`. Do not call
// `fireEvent.mouseMove` directly, because these helpers own one detail that is
// silently destructive to get wrong:
//
//   `buttons: 1` on every move. Results.tsx ends a gesture on the first move
//   that arrives with no button held — that is how it recovers from a mouseup
//   released outside the window. A move fired WITHOUT `buttons` therefore ends
//   the gesture immediately, with `delta === null`, so NOTHING is written and
//   no error is raised. A regression test shaped "assert the preference was
//   not destroyed" would then pass vacuously, forever, against any bug.
//   `ends the gesture on a move with no button held` below pins that
//   behaviour so this trap stays visible.
//
// `mouseDown` also carries `button: 0`; the handler ignores non-primary
// buttons.
const DRAG_START_X = 900

function beginDrag() {
  fireEvent.mouseDown(screen.getByTestId('compare-rail-splitter'), {
    clientX: DRAG_START_X, button: 0, buttons: 1,
  })
  let lastDx = 0
  return {
    move(dx: number) {
      lastDx = dx
      fireEvent.mouseMove(window, { clientX: DRAG_START_X + dx, buttons: 1 })
    },
    /** A layout event with the button still held. */
    interleave(fn: () => void) { act(() => { fn() }) },
    release() {
      fireEvent.mouseUp(window, { clientX: DRAG_START_X + lastDx })
    },
    /** The pointer returns over the document after an off-window release. */
    loseButton() {
      fireEvent.mouseMove(window, { clientX: DRAG_START_X + lastDx, buttons: 0 })
    },
  }
}

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
function dragSplitter(...steps: DragStep[]) {
  const g = beginDrag()
  for (const step of steps) {
    if (typeof step === 'function') g.interleave(step)
    else g.move(step)
  }
  g.release()
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

    const g = beginDrag()
    g.move(+100)

    expect(renderedWidth()).toBe(600)
    expect(useUIStore.getState().compareRailWidth).toBe(700)

    g.release()
    expect(useUIStore.getState().compareRailWidth).toBe(600)
  })

  it('records nothing when the splitter is clicked without moving', () => {
    // A bare click must not store `startW` — which is the CONSTRAINED width,
    // so on a constrained rail a stray click would overwrite the preference
    // with the ceiling. That is round 2's defect via a different door.
    renderResults()
    act(openDockTo(WRAP_1440_DOCKED))
    expect(renderedWidth()).toBe(460)

    beginDrag().release()

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

  // ── Widening a CONSTRAINED rail ─────────────────────────────────────────
  //
  // Wrapper 820, ceiling 460, stored 700, rendered 460. `startW` is 460, so
  // `startW + delta` is BELOW the stored 700 for any drag shorter than the
  // 240px constraint gap. Recording that loses width in the opposite
  // direction from the gesture, and the rail renders 460 either way, so
  // nothing on screen betrays it.
  it('loses nothing when a small widening nudge is made on a constrained rail', () => {
    // 1px. The worst case, and well inside trackpad click-drag jitter: the
    // naive value is 461, a 239px silent loss.
    renderResults()
    act(openDockTo(WRAP_1440_DOCKED))
    expect(renderedWidth()).toBe(460)

    dragSplitter(-1)

    expect(useUIStore.getState().compareRailWidth).toBe(700)
    expect(localStorage.getItem(WIDTH_KEY)).toBe('700')
    expect(renderedWidth()).toBe(460)
  })

  it('loses nothing on a widening drag shorter than the constraint gap', () => {
    renderResults()
    act(openDockTo(WRAP_1440_DOCKED))

    dragSplitter(-200)

    expect(useUIStore.getState().compareRailWidth).toBe(700)

    act(() => {
      setViewportWidth(WRAP_1440_UNDOCKED)
      useUIStore.getState().setAssistantDockOpen(false)
    })
    expect(renderedWidth()).toBe(700)
  })

  it('treats a drag that returns to its origin as asking for no change', () => {
    renderResults()
    act(openDockTo(WRAP_1440_DOCKED))

    dragSplitter(-150, 0)

    expect(useUIStore.getState().compareRailWidth).toBe(700)
  })

  it('stores a width LARGER than fits once the drag exceeds the stored width', () => {
    // The intended consequence of keeping the ceiling out of the write path:
    // the user asks for wider than fits, we store it, the render shows what
    // fits, and they get it back when the dock closes.
    renderResults()
    act(openDockTo(WRAP_1440_DOCKED))

    dragSplitter(-500)

    expect(useUIStore.getState().compareRailWidth).toBe(960)
    expect(renderedWidth()).toBe(460)

    act(() => {
      setViewportWidth(WRAP_1440_UNDOCKED)
      useUIStore.getState().setAssistantDockOpen(false)
    })
    expect(renderedWidth()).toBe(840) // ceiling(1200); desired is 960
  })

  it('records a SHRINK on a constrained rail exactly as asked', () => {
    // The guard protects widening only. Asking for narrower is visible on
    // screen and must be recorded.
    renderResults()
    act(openDockTo(WRAP_1440_DOCKED))

    dragSplitter(+50)

    expect(useUIStore.getState().compareRailWidth).toBe(410)
    expect(renderedWidth()).toBe(410)
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

    const g = beginDrag()
    g.move(-200)
    // Desired 900, but the render constrains the preview exactly like it
    // constrains the resting width: ceiling(1200) = 840. The handle stops
    // following once there is no room, which is correct and is the same rule
    // at rest and in flight.
    expect(renderedWidth()).toBe(840)

    g.interleave(openDockTo(WRAP_1440_DOCKED))

    expect(renderedWidth()).toBe(460)

    g.release()
    expect(useUIStore.getState().compareRailWidth).toBe(900)
  })

  it('finishes a gesture whose mouseup was lost, instead of freezing the preview', () => {
    // Released outside the window: `onUp` never fires. The next move over the
    // document arrives with no button held, which is the only signal we get.
    // Without acting on it the preview stays frozen over a store that never
    // agreed to it — through dock toggles and resizes — and the listeners
    // stay live.
    renderResults()
    const g = beginDrag()
    g.move(-200)
    expect(renderedWidth()).toBe(840)
    expect(useUIStore.getState().compareRailWidth).toBe(700)

    // Button released off-window; the pointer comes back over the document.
    g.loseButton()

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
    const abandoned = beginDrag()
    abandoned.move(-200)

    act(openDockTo(WRAP_1440_DOCKED))

    dragSplitter(+100)

    // Only the second gesture's release counts: startW 460, dragged right 100.
    expect(useUIStore.getState().compareRailWidth).toBe(360)
  })

  it('does not write the constraint on a narrow wrapper', () => {
    // Wrapper 660: the ceiling IS the floor, so the rail cannot move at all.
    // A widening drag shorter than the gap must still leave the stored width
    // alone rather than recording the floor.
    renderResults()
    act(openDockTo(WRAP_1280_DOCKED))
    expect(renderedWidth()).toBe(RAIL_MIN_W)

    dragSplitter(-100)
    expect(useUIStore.getState().compareRailWidth).toBe(700)

    dragSplitter(-500)
    expect(useUIStore.getState().compareRailWidth).toBe(860)
    expect(renderedWidth()).toBe(RAIL_MIN_W)
  })

  // ── Layout events INSIDE a gesture, with the drag continuing ────────────
  it('keeps tracking the pointer after a layout event lands mid-drag', () => {
    // The shape the thunk harness exists for, and the one that was untested:
    // every other interleaved case fires its layout event as the LAST step.
    // Here the dock opens and the user keeps dragging afterwards, so the
    // moves that follow have to be measured against the same origin — the
    // gesture's origin, not the new layout's.
    renderResults()

    const g = beginDrag()
    g.move(-100)
    expect(renderedWidth()).toBe(800)

    g.interleave(openDockTo(WRAP_1440_DOCKED))
    expect(renderedWidth()).toBe(460) // constrained by the NEW wrapper

    g.move(-300)
    // Origin is still the gesture's startW (700), so desired is 1000; the
    // render constrains it to the new ceiling.
    expect(renderedWidth()).toBe(460)

    g.release()
    expect(useUIStore.getState().compareRailWidth).toBe(1000)
  })

  // ── The harness trap ────────────────────────────────────────────────────
  it('ends the gesture on a move with no button held', () => {
    // Pins the behaviour that makes `buttons: 1` mandatory in the helpers.
    // A test that forgets it ends the drag on its first move with delta null,
    // so NOTHING is written and no error is raised — which would make any
    // "the preference survived" assertion pass vacuously forever.
    renderResults()

    const g = beginDrag()
    g.loseButton()          // first move, no button: gesture over, nothing recorded
    g.move(-200)            // too late; the listeners are gone
    g.release()

    expect(useUIStore.getState().compareRailWidth).toBe(700)
    expect(renderedWidth()).toBe(700)
  })

  it('ignores a non-primary button drag', () => {
    // Middle-click drag (autoscroll on Windows/Linux) must not resize or
    // write.
    renderResults()

    fireEvent.mouseDown(screen.getByTestId('compare-rail-splitter'), {
      clientX: DRAG_START_X, button: 1, buttons: 4,
    })
    fireEvent.mouseMove(window, { clientX: DRAG_START_X - 200, buttons: 4 })
    fireEvent.mouseUp(window, { clientX: DRAG_START_X - 200 })

    expect(useUIStore.getState().compareRailWidth).toBe(700)
    expect(renderedWidth()).toBe(700)
  })
})
