// P21 — the guided tour: navigation, missing / optional / revealed targets,
// keyboard, persisted "seen", and catalogue-driven hover text.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
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
