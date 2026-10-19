import { describe, it, expect, beforeEach, vi } from 'vitest'

const { readiness } = vi.hoisted(() => ({
  readiness: { current: { effectiveProfileId: null as string | null, ready: undefined as boolean | undefined, settled: false } },
}))
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useUIStore } from '../store/uiStore'

vi.mock('./ChatPanel', () => ({ default: () => <div data-testid="chat-panel-stub">chat</div> }))

vi.mock('../hooks/useChatProfiles', () => ({
  useChatProfiles: () => ({ data: undefined, isError: false }),
  useChatReadiness: () => readiness.current,
}))

import AssistantDock from './AssistantDock'

describe('AssistantDock while model readiness is still unknown', () => {
  beforeEach(() => {
    localStorage.clear()
    useUIStore.setState({ assistantDockOpen: false, assistantEntry: null, resultsTabRequest: null })
    // Readiness not answered yet: a configured model may still be on its way.
    readiness.current = { effectiveProfileId: null, ready: undefined, settled: false }
  })

  // Q4: the local tour and picker are for "no key", which is not yet known.
  it.each(['Open the project', 'Walk me through the key results in each tab'])
  ('neither runs a local shortcut nor sends: %s', async (text) => {
    const user = userEvent.setup()
    const heard: string[] = []
    const picker = vi.fn()
    const onUtterance = (ev: Event) => heard.push((ev as CustomEvent<string>).detail)
    window.addEventListener('companion:live-utterance', onUtterance)
    window.addEventListener('chat:open-project-picker', picker)
    const view = render(<AssistantDock />)
    try {
      await user.click(screen.getByTestId('companion-live'))
      await user.type(screen.getByTestId('companion-live-input'), text)
      await user.click(screen.getByTestId('companion-live-send'))
      expect(heard).toEqual([])
      expect(picker).not.toHaveBeenCalled()
      expect(useUIStore.getState().resultsTabRequest).toBeNull()
      expect(screen.getByTestId('companion-live-caption').textContent).toBe(
        'Still checking the assistant. Nothing was sent; try again in a moment.')
    } finally {
      view.unmount()
      window.removeEventListener('companion:live-utterance', onUtterance)
      window.removeEventListener('chat:open-project-picker', picker)
    }
  })

  it('treats answered readiness with no usable profile as the no-key path', async () => {
    readiness.current = { effectiveProfileId: null, ready: undefined, settled: true }
    const user = userEvent.setup()
    const picker = vi.fn()
    window.addEventListener('chat:open-project-picker', picker)
    const view = render(<AssistantDock />)
    try {
      await user.click(screen.getByTestId('companion-live'))
      await user.type(screen.getByTestId('companion-live-input'), 'Open the project')
      await user.click(screen.getByTestId('companion-live-send'))
      expect(picker).toHaveBeenCalledTimes(1)
      expect(screen.getByTestId('companion-live-caption').textContent).toBe('Opening the project list.')
    } finally {
      view.unmount()
      window.removeEventListener('chat:open-project-picker', picker)
    }
  })
})
