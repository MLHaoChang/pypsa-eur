import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useEffect } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useUIStore } from '../store/uiStore'

const { chatPanelMounts } = vi.hoisted(() => ({ chatPanelMounts: { current: 0 } }))

vi.mock('./ChatPanel', () => ({
  default: () => {
    useEffect(() => {
      chatPanelMounts.current += 1
    }, [])
    return <div data-testid="chat-panel-stub">chat</div>
  },
}))

vi.mock('../hooks/useChatProfiles', () => ({
  useChatProfiles: () => ({
    data: { profiles: [{ id: 'claude', label: 'Claude', wire: 'anthropic' as const }], active_profile_id: 'claude' },
    isError: false,
  }),
  useChatReadiness: () => ({ effectiveProfileId: 'claude', ready: false }),
}))

import AssistantDock from './AssistantDock'

describe('AssistantDock without API key', () => {
  beforeEach(() => {
    localStorage.clear()
    useUIStore.setState({ assistantDockOpen: false, assistantEntry: null })
    chatPanelMounts.current = 0
  })

  it('does not send a general live sentence when the model key is missing', async () => {
    const user = userEvent.setup()
    const heard: string[] = []
    window.addEventListener('companion:live-utterance', (ev: Event) => {
      heard.push((ev as CustomEvent<string>).detail)
    })
    render(<AssistantDock />)
    await user.click(screen.getByTestId('companion-live'))
    await user.type(screen.getByTestId('companion-live-input'), 'List the generators')
    await user.click(screen.getByTestId('companion-live-send'))
    expect(heard).toEqual([])
    expect(useUIStore.getState().assistantDockOpen).toBe(false)
    expect(screen.getByTestId('companion-live-caption').textContent).toContain('Add an API key')
  })

  it('opens the project picker locally without a model key', async () => {
    const user = userEvent.setup()
    const heard: string[] = []
    const picker = vi.fn()
    const onUtterance = (ev: Event) => heard.push((ev as CustomEvent<string>).detail)
    window.addEventListener('companion:live-utterance', onUtterance)
    window.addEventListener('chat:open-project-picker', picker)
    const view = render(<AssistantDock />)
    try {
      await user.click(screen.getByTestId('companion-live'))
      await user.type(screen.getByTestId('companion-live-input'), 'Open the project')
      await user.click(screen.getByTestId('companion-live-send'))
      expect(picker).toHaveBeenCalledTimes(1)
      expect(heard).toEqual([])
      expect(screen.getByTestId('companion-live-caption').textContent).toBe('Opening the project list.')
    } finally {
      view.unmount()
      window.removeEventListener('companion:live-utterance', onUtterance)
      window.removeEventListener('chat:open-project-picker', picker)
    }
  })

  it('runs the local results tour without a model key and keeps Guided mode', async () => {
    const user = userEvent.setup()
    const heard: string[] = []
    const onUtterance = (ev: Event) => heard.push((ev as CustomEvent<string>).detail)
    window.addEventListener('companion:live-utterance', onUtterance)
    useUIStore.setState({ currentProject: 'Demo', discussedRun: null,
      activeSlidePanel: null, resultsTabRequest: null, uiMode: 'guided' })
    const view = render(<AssistantDock />)
    try {
      await user.click(screen.getByTestId('companion-live'))
      await user.type(screen.getByTestId('companion-live-input'), 'Walk me through the key results in each tab')
      await user.click(screen.getByTestId('companion-live-send'))
      expect(heard).toEqual([])
      expect(useUIStore.getState().uiMode).toBe('guided')
      expect(screen.getByTestId('companion-live-caption').textContent).toContain('Guided mode')
    } finally {
      view.unmount()
      window.removeEventListener('companion:live-utterance', onUtterance)
    }
  })
})
