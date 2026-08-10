import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useEffect } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useUIStore } from '../store/uiStore'

// Mutable counter reachable from inside the vi.mock factory below. Plain
// top-level `let`/`const` bindings are off-limits there (the factory is
// hoisted above this file's imports, so vitest's static check rejects any
// reference to a locally-declared variable that isn't produced by
// vi.hoisted()) — this is the documented way to share mutable state with a
// hoisted mock factory.
const { chatPanelMounts } = vi.hoisted(() => ({ chatPanelMounts: { current: 0 } }))

vi.mock('./ChatPanel', () => ({
  default: () => {
    // Counts *mounts*, not renders: an empty dep array only fires once per
    // instance. A remount at the same position in the tree (e.g. an
    // ErrorBoundary keyed on assistantDockOpen) bumps this a second time,
    // which is exactly the regression the identity test below pins.
    useEffect(() => {
      chatPanelMounts.current += 1
    }, [])
    return <div data-testid="chat-panel-stub">chat</div>
  },
}))

import AssistantDock from './AssistantDock'

describe('AssistantDock', () => {
  beforeEach(() => {
    localStorage.clear()
    useUIStore.setState({ assistantDockOpen: false })
    chatPanelMounts.current = 0
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
    // CommandPalette.test.tsx's ArrowDown highlight assertions, which match
    // on className for the same reason). It is also the more accurate check in this
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

  // The four tests above query for DOM *presence*, which catches a
  // conditional-mount regression ({assistantDockOpen && <ChatPanel />}) but
  // would stay green even if ChatPanel were unmounted and immediately
  // remounted at the same position — e.g. by keying the ErrorBoundary on
  // assistantDockOpen. A same-position remount kills a streaming turn the
  // same way an unmount does (see ChatPanel.tsx's SSE-cleanup effect), so
  // instance identity across a toggle is itself load-bearing.
  it('keeps the same ChatPanel instance across a collapse/expand cycle', async () => {
    const user = userEvent.setup()
    render(<AssistantDock />)
    expect(chatPanelMounts.current).toBe(1)

    await user.click(screen.getByTestId('assistant-dock-launcher'))
    expect(useUIStore.getState().assistantDockOpen).toBe(true)
    expect(chatPanelMounts.current).toBe(1)

    await user.click(screen.getByTestId('assistant-dock-collapse'))
    expect(useUIStore.getState().assistantDockOpen).toBe(false)
    expect(chatPanelMounts.current).toBe(1)
  })
})
