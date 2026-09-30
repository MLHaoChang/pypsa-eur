// P21 — the guided tour: navigation, missing / optional / revealed targets,
// keyboard, persisted "seen", and catalogue-driven hover text.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  GuideButton, guidesApi, tourSeen, useGuideField, type GuideCatalogue,
} from './GuidedTour'

const CATALOGUE: GuideCatalogue = {
  version: 1,
  tours: {
    demo: {
      title: 'Demo tour',
      intro: 'Welcome.',
      steps: [
        { target: 'a', title: 'Step A', body: 'Body A', enter: 'Enter A' },
        { target: 'maybe', title: 'Optional', body: 'Only if present', optional: true },
        { target: 'hidden', reveal: 'opener', title: 'Step H', body: 'Revealed' },
        { target: 'absent', title: 'Step X', body: 'Not on screen', after_run: true },
      ],
    },
  },
  fields: { eh_poc: 'Catalogue PoC text' },
}

function Harness() {
  const [shown, setShown] = useState(false)
  return (
    <div>
      <div data-testid="a" style={{ width: 10, height: 10 }}>A</div>
      <input data-testid="field" />
      <button data-testid="opener" onClick={() => setShown(true)}>open</button>
      {shown && <div data-testid="hidden">H</div>}
      <GuideButton tourId="demo" testId="start" />
    </div>
  )
}

function renderWith(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

beforeEach(() => {
  vi.spyOn(guidesApi, 'getGuide').mockResolvedValue(CATALOGUE)
  try { window.localStorage.clear() } catch { /* ignore */ }
  // jsdom has no layout: give every element a box so targets count as shown.
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(
    { top: 10, left: 10, width: 50, height: 20, right: 60, bottom: 30, x: 10, y: 10,
      toJSON: () => ({}) } as DOMRect)
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('GuidedTour', () => {
  it('walks the steps: intro, enter hint, optional skip, reveal, missing target', async () => {
    const user = userEvent.setup()
    renderWith(<Harness />)
    await user.click(screen.getByTestId('start'))
    expect((await screen.findByTestId('guide-step-title')).textContent).toBe('Step A')
    expect(screen.getByTestId('guide-intro').textContent).toBe('Welcome.')
    expect(screen.getByTestId('guide-step-enter').textContent).toMatch(/Enter A/)
    expect(screen.getByTestId('guide-highlight')).toBeTruthy()
    expect(screen.getByTestId('guide-tour').textContent).toMatch(/1\/3/)  // optional dropped

    await user.click(screen.getByTestId('guide-next'))
    expect(screen.getByTestId('guide-step-title').textContent).toBe('Step H')
    expect(screen.getByTestId('hidden')).toBeTruthy()                    // revealed
    await waitFor(() => expect(screen.queryByTestId('guide-step-missing')).toBeNull())

    await user.click(screen.getByTestId('guide-next'))
    expect(screen.getByTestId('guide-step-title').textContent).toBe('Step X')
    expect(screen.getByTestId('guide-step-missing').textContent)        // no crash
      .toMatch(/appears after a study has run/)
    expect(screen.getByTestId('guide-next').textContent).toBe('Done')

    await user.click(screen.getByTestId('guide-back'))
    expect(screen.getByTestId('guide-step-title').textContent).toBe('Step H')
  })

  it('marks the tour seen only when finished', async () => {
    const user = userEvent.setup()
    renderWith(<Harness />)
    await user.click(screen.getByTestId('start'))
    await screen.findByTestId('guide-tour')
    await user.click(screen.getByTestId('guide-skip'))
    expect(tourSeen('demo')).toBe(false)
    await user.click(screen.getByTestId('start'))
    for (let i = 0; i < 3; i++) await user.click(await screen.findByTestId('guide-next'))
    await waitFor(() => expect(screen.queryByTestId('guide-tour')).toBeNull())
    expect(tourSeen('demo')).toBe(true)
  })

  it('closes on Escape and steps with the arrow keys', async () => {
    const user = userEvent.setup()
    renderWith(<Harness />)
    await user.click(screen.getByTestId('start'))
    await screen.findByTestId('guide-tour')
    await user.keyboard('{ArrowRight}')
    expect(screen.getByTestId('guide-step-title').textContent).toBe('Step H')
    await user.keyboard('{ArrowLeft}')
    expect(screen.getByTestId('guide-step-title').textContent).toBe('Step A')
    await user.keyboard('{Escape}')
    expect(screen.queryByTestId('guide-tour')).toBeNull()
  })
})

describe('useGuideField', () => {
  function Tip({ k }: { k: string }) {
    return <span data-testid="tip">{useGuideField(k, 'fallback')}</span>
  }
  it('uses the catalogue wording, else the fallback', async () => {
    renderWith(<><Tip k="eh_poc" /></>)
    await waitFor(() => expect(screen.getByTestId('tip').textContent)
      .toBe('Catalogue PoC text'))
    cleanup()
    renderWith(<Tip k="unknown" />)
    expect(screen.getByTestId('tip').textContent).toBe('fallback')
  })
})


it('never hijacks keys typed into a form field', async () => {
  const user = userEvent.setup()
  renderWith(<Harness />)
  await user.click(screen.getByTestId('start'))
  await screen.findByTestId('guide-tour')
  screen.getByTestId('field').focus()
  await user.keyboard('{ArrowRight}{Escape}')
  expect(screen.getByTestId('guide-tour')).toBeTruthy()
  expect(screen.getByTestId('guide-step-title').textContent).toBe('Step A')
})

// ── P30 (B4): the popover is measured and placed by fit ─────────────────────
// Deferred spec 2026-09-28 §5.1: candidates below, above, right, left (12 px
// gap); the first that fits inside the viewport (8 px margin) without
// touching the highlight box wins; none → `free`, still inside the viewport.
describe('GuidedTour placement (P30 B4)', () => {
  const PLACE_CAT: GuideCatalogue = {
    version: 1,
    tours: { place: { title: 'Place', steps: [{ target: 't', title: 'T', body: 'Body' }] } },
    fields: {},
  }
  type Box = { top: number; left: number; width: number; height: number }
  let saved: { w: number; h: number }
  beforeEach(() => { saved = { w: window.innerWidth, h: window.innerHeight } })
  afterEach(() => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: saved.w })
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: saved.h })
  })

  function layout(vw: number, vh: number, target: Box, pop: { width: number; height: number }) {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: vw })
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: vh })
    vi.mocked(guidesApi.getGuide).mockResolvedValue(PLACE_CAT)
    vi.mocked(HTMLElement.prototype.getBoundingClientRect).mockImplementation(function (this: HTMLElement) {
      const id = this.getAttribute('data-testid')
      const b = id === 't' ? target : id === 'guide-tour' ? { top: 0, left: 0, ...pop }
        : { top: 0, left: 0, width: 0, height: 0 }
      return { ...b, right: b.left + b.width, bottom: b.top + b.height, x: b.left, y: b.top,
        toJSON: () => ({}) } as DOMRect
    })
  }

  async function open() {
    const user = userEvent.setup()
    renderWith(<div><div data-testid="t">T</div><GuideButton tourId="place" testId="start" /></div>)
    await user.click(screen.getByTestId('start'))
    const tour = await screen.findByTestId('guide-tour')
    await waitFor(() => expect(tour.getAttribute('data-placement')).toBeTruthy())
    return tour
  }

  function box(tour: HTMLElement, pop: { width: number; height: number }, vw: number, vh: number) {
    const top = parseFloat(tour.style.top)
    const left = parseFloat(tour.style.left)
    return { top, left, w: Math.min(pop.width, vw - 16), h: Math.min(pop.height, vh - 16) }
  }

  function assertInside(b: { top: number; left: number; w: number; h: number }, vw: number, vh: number) {
    expect(b.top).toBeGreaterThanOrEqual(8)
    expect(b.top + b.h).toBeLessThanOrEqual(vh - 8)
    expect(b.left).toBeGreaterThanOrEqual(8)
    expect(b.left + b.w).toBeLessThanOrEqual(vw - 8)
  }

  function assertApart(b: { top: number; left: number; w: number; h: number }, t: Box) {
    // the highlight is the target grown by 4 px on every side
    const hl = { top: t.top - 4, left: t.left - 4, bottom: t.top + t.height + 4, right: t.left + t.width + 4 }
    const apart = b.left + b.w <= hl.left || b.left >= hl.right
      || b.top + b.h <= hl.top || b.top >= hl.bottom
    expect(apart).toBe(true)
  }

  it('a target at the bottom with a tall popover → above, apart from the target', async () => {
    const t = { top: 700, left: 100, width: 200, height: 30 }
    const pop = { width: 320, height: 300 }
    layout(1024, 768, t, pop)
    const tour = await open()
    await waitFor(() => expect(tour.getAttribute('data-placement')).toBe('above'))
    const b = box(tour, pop, 1024, 768)
    assertInside(b, 1024, 768)
    assertApart(b, t)
  })

  it('a tall target at the right edge → left', async () => {
    const t = { top: 100, left: 1000, width: 20, height: 568 }
    const pop = { width: 320, height: 200 }
    layout(1024, 768, t, pop)
    const tour = await open()
    await waitFor(() => expect(tour.getAttribute('data-placement')).toBe('left'))
    const b = box(tour, pop, 1024, 768)
    assertInside(b, 1024, 768)
    assertApart(b, t)
  })

  it('room below → below (the default), apart from the target', async () => {
    const t = { top: 40, left: 40, width: 100, height: 20 }
    const pop = { width: 320, height: 240 }
    layout(1024, 768, t, pop)
    const tour = await open()
    await waitFor(() => expect(tour.getAttribute('data-placement')).toBe('below'))
    const b = box(tour, pop, 1024, 768)
    assertInside(b, 1024, 768)
    assertApart(b, t)
  })

  it('a tiny viewport → free, still inside the viewport, height capped', async () => {
    const t = { top: 80, left: 100, width: 100, height: 40 }
    const pop = { width: 320, height: 300 }
    layout(300, 200, t, pop)
    const tour = await open()
    await waitFor(() => expect(tour.getAttribute('data-placement')).toBe('free'))
    assertInside(box(tour, pop, 300, 200), 300, 200)
    expect(tour.style.maxHeight).toBe('calc(100vh - 16px)')
    expect(tour.style.overflowY).toBe('auto')
  })

  // P30 smoke finding: centred, the popover of a step whose target left the
  // screen covered the Properties panel's Edit button the step asks for. It
  // docks in the bottom-left corner instead, inside the viewport.
  it('a target not on screen → docked bottom-left, inside the viewport', async () => {
    const pop = { width: 320, height: 300 }
    layout(1440, 900, { top: 0, left: 0, width: 0, height: 0 }, pop)
    const tour = await open()
    await waitFor(() => expect(tour.style.top).toBe(`${900 - 8 - 300}px`))
    expect(tour.getAttribute('data-placement')).toBe('free')
    expect(tour.style.left).toBe('8px')
    expect(screen.queryByTestId('guide-highlight')).toBeNull()
    assertInside(box(tour, pop, 1440, 900), 1440, 900)
  })

  it('re-measures: a popover that grows moves from below to above', async () => {
    const t = { top: 400, left: 100, width: 200, height: 30 }
    let pop = { width: 320, height: 150 }
    layout(1024, 768, t, pop)
    const tour = await open()
    await waitFor(() => expect(tour.getAttribute('data-placement')).toBe('below'))
    pop = { width: 320, height: 330 }
    layout(1024, 768, t, pop)
    act(() => { window.dispatchEvent(new Event('resize')) })
    await waitFor(() => expect(tour.getAttribute('data-placement')).toBe('above'))
    const b = box(tour, pop, 1024, 768)
    assertInside(b, 1024, 768)
    assertApart(b, t)
  })
})

// ── P30 (B7): optional steps are judged when they are reached ───────────────
describe('GuidedTour optional steps (P30 B7)', () => {
  const LATE: GuideCatalogue = {
    version: 1,
    tours: {
      late: { title: 'Late', steps: [
        { target: 'first', title: 'First', body: 'one' },
        { target: 'gone', title: 'Gone', body: 'never here', optional: true },
        { target: 'late', title: 'Late', body: 'appears later', optional: true },
      ] },
    },
    fields: {},
  }
  function LateHarness() {
    const [shown, setShown] = useState(false)
    return (
      <div>
        <div data-testid="first">F</div>
        <button data-testid="show-late" onClick={() => setShown(true)}>show</button>
        {shown && <div data-testid="late">L</div>}
        <GuideButton tourId="late" testId="start" />
      </div>
    )
  }

  it('an optional step whose target appears after the tour started is shown', async () => {
    vi.mocked(guidesApi.getGuide).mockResolvedValue(LATE)
    const user = userEvent.setup()
    renderWith(<LateHarness />)
    await user.click(screen.getByTestId('start'))
    await screen.findByTestId('guide-tour')
    expect(screen.getByTestId('guide-tour').textContent).toMatch(/1\/1/)
    await user.click(screen.getByTestId('show-late'))
    await waitFor(() => expect(screen.getByTestId('guide-tour').textContent).toMatch(/1\/2/))
    expect(screen.getByTestId('guide-next').textContent).toBe('Next')
    await user.click(screen.getByTestId('guide-next'))
    expect(screen.getByTestId('guide-step-title').textContent).toBe('Late')
    expect(screen.getByTestId('guide-tour').textContent).toMatch(/2\/2/)
    expect(screen.getByTestId('guide-next').textContent).toBe('Done')
    await user.click(screen.getByTestId('guide-back'))
    expect(screen.getByTestId('guide-step-title').textContent).toBe('First')   // `gone` skipped
  })

  it('the counter counts only visible steps', async () => {
    vi.mocked(guidesApi.getGuide).mockResolvedValue(LATE)
    const user = userEvent.setup()
    renderWith(<LateHarness />)
    await user.click(screen.getByTestId('start'))
    await screen.findByTestId('guide-tour')
    // two optional steps absent: one visible step, and Next finishes the tour
    expect(screen.getByTestId('guide-tour').textContent).toMatch(/Late · 1\/1/)
    expect(screen.getByTestId('guide-next').textContent).toBe('Done')
    await user.click(screen.getByTestId('guide-next'))
    await waitFor(() => expect(screen.queryByTestId('guide-tour')).toBeNull())
    expect(tourSeen('late')).toBe(true)
  })
})
