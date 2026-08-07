import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useUIStore } from '../store/uiStore'

vi.mock('./ChatPanel', () => ({
  default: () => <div data-testid="chat-panel-stub">chat</div>,
}))

import AssistantDock from './AssistantDock'

describe('AssistantDock', () => {
  beforeEach(() => {
    localStorage.clear()
    useUIStore.setState({ assistantDockOpen: false })
  })

  it('shows the launcher when collapsed', () => {
    render(<AssistantDock />)
    // No @testing-library/jest-dom in this repo (see every other
    // *.test.tsx) — getByTestId already throws if the node is absent, so
    // toBeTruthy() (built into vitest's expect) confirms the query
    // succeeded, matching this codebase's convention.
    expect(screen.getByTestId('assistant-dock-launcher')).toBeTruthy()
  })

  it('keeps ChatPanel mounted while collapsed', () => {
    render(<AssistantDock />)
    expect(screen.getByTestId('chat-panel-stub')).toBeTruthy()
  })

  it('expands when the launcher is clicked', async () => {
    const user = userEvent.setup()
    render(<AssistantDock />)
    await user.click(screen.getByTestId('assistant-dock-launcher'))
    expect(useUIStore.getState().assistantDockOpen).toBe(true)
    // No jest-dom toBeVisible() here either; this repo's own convention for
    // CSS-class-driven visibility is a className check (see
    // CommandPalette.test.tsx). It is also the more accurate check in this
    // suite: vitest's jsdom environment doesn't load Tailwind's stylesheet
    // (no `css: true`), so getComputedStyle-based visibility checks would
    // report "visible" regardless of the `hidden` class. Splitting on
    // whitespace (rather than a regex) avoids a false match against the
    // unrelated `overflow-hidden` utility class also present here.
    expect(screen.getByTestId('assistant-dock-body').className.split(/\s+/)).not.toContain('hidden')
  })

  it('collapses again from the header control', async () => {
    const user = userEvent.setup()
    useUIStore.setState({ assistantDockOpen: true })
    render(<AssistantDock />)
    await user.click(screen.getByTestId('assistant-dock-collapse'))
    expect(useUIStore.getState().assistantDockOpen).toBe(false)
    expect(screen.getByTestId('chat-panel-stub')).toBeTruthy()
    expect(screen.getByTestId('assistant-dock-body').className.split(/\s+/)).toContain('hidden')
  })
})
