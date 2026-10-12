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
})
