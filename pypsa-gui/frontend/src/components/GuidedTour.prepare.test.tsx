// Obstacle 3 (guided-mode spec §2.7): a tour may need the app in a certain
// state before its first step (the tagging tour selects a bus and opens the
// Properties panel's Edit form). `GuideButton.prepare` runs and is awaited
// before the tour mounts. Because `prepare` may unmount the button itself
// (it closes the Results panel the button lives in), the prepared tour is
// rendered by the app-level `GuidedTourHost`, not by the button.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  GuideButton, GuidedTour, GuidedTourHost, guidesApi, type GuideCatalogue,
} from './GuidedTour'

const CATALOGUE: GuideCatalogue = {
  version: 1,
  tours: {
    tag: {
      title: 'Tagging',
      steps: [
        { target: 'fields', reveal: 'edit-btn', title: 'Bus tags', body: 'Tick them.' },
        { target: 'link-role', reveal: 'link-edit', title: 'Link role', body: 'Pick one.',
          optional: true },
      ],
    },
    guard: {
      title: 'Guard',
      steps: [{ target: 'nowhere', reveal: 'also-nowhere', title: 'Lonely', body: '…' }],
    },
  },
  fields: {},
}

function renderWith(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

beforeEach(() => {
  vi.spyOn(guidesApi, 'getGuide').mockResolvedValue(CATALOGUE)
  try { window.localStorage.clear() } catch { /* ignore */ }
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(
    { top: 10, left: 10, width: 50, height: 20, right: 60, bottom: 30, x: 10, y: 10,
      toJSON: () => ({}) } as DOMRect)
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

/** The button lives in a "panel" that `prepare` closes, like Results. */
function App({ prepare }: { prepare: () => void | Promise<void> }) {
  const [panelOpen, setPanelOpen] = useState(true)
  const [fields, setFields] = useState(false)
  return (
    <div>
      {panelOpen && (
        <GuideButton tourId="tag" testId="tag-start"
          prepare={async () => { await prepare(); setPanelOpen(false); setFields(true) }} />
      )}
      {fields && <div data-testid="fields">EH fields</div>}
      <GuidedTourHost />
    </div>
  )
}

describe('GuideButton.prepare', () => {
  it('awaits prepare before the first step renders, and survives the button unmounting', async () => {
    const user = userEvent.setup()
    let release!: () => void
    const gate = new Promise<void>(r => { release = r })
    const prepare = vi.fn(() => gate)
    renderWith(<App prepare={prepare} />)
    await user.click(screen.getByTestId('tag-start'))
    expect(prepare).toHaveBeenCalledTimes(1)
    // prepare has not resolved: no tour yet.
    expect(screen.queryByTestId('guide-tour')).toBeNull()
    await act(async () => { release() })
    expect((await screen.findByTestId('guide-step-title')).textContent).toBe('Bus tags')
    expect(screen.queryByTestId('tag-start')).toBeNull()        // the button is gone
    expect(screen.queryByTestId('guide-step-missing')).toBeNull()  // target was ready
    // The optional Link step (target absent) is dropped: one step only.
    expect(screen.getByTestId('guide-tour').textContent).toMatch(/1\/1/)
    await user.click(screen.getByTestId('guide-next'))
    expect(screen.queryByTestId('guide-tour')).toBeNull()
  })

  it('a rejected prepare still opens the tour, with the missing-target note', async () => {
    const user = userEvent.setup()
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    renderWith(
      <div>
        <GuideButton tourId="tag" testId="tag-start"
          prepare={() => Promise.reject(new Error('no buses'))} />
        <GuidedTourHost />
      </div>,
    )
    await user.click(screen.getByTestId('tag-start'))
    expect((await screen.findByTestId('guide-step-title')).textContent).toBe('Bus tags')
    expect(screen.getByTestId('guide-step-missing')).toBeTruthy()
  })

  it('without prepare the button still renders its own tour (unchanged)', async () => {
    const user = userEvent.setup()
    renderWith(<div><div data-testid="fields" /><GuideButton tourId="tag" testId="plain" /></div>)
    await user.click(screen.getByTestId('plain'))
    expect((await screen.findByTestId('guide-step-title')).textContent).toBe('Bus tags')
  })
})

describe('reveal guard', () => {
  it('does not click a reveal that is absent from the DOM', async () => {
    const click = vi.spyOn(HTMLElement.prototype, 'click')
    renderWith(<div><button data-testid="bystander" /><GuidedTour tourId="guard" onClose={() => {}} /></div>)
    expect((await screen.findByTestId('guide-step-title')).textContent).toBe('Lonely')
    expect(screen.getByTestId('guide-step-missing')).toBeTruthy()
    expect(click).not.toHaveBeenCalled()
  })
})
