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

vi.mock('../hooks/useChatProfiles', () => ({
  useChatProfiles: () => ({
    data: {
      profiles: [{ id: 'claude', label: 'Claude', wire: 'anthropic' as const }],
      active_profile_id: 'claude',
    },
    isError: false,
  }),
  useChatReadiness: () => ({ effectiveProfileId: 'claude', ready: true }),
}))

import AssistantDock from './AssistantDock'

describe('AssistantDock', () => {
  beforeEach(() => {
    localStorage.clear()
    useUIStore.setState({ assistantDockOpen: false, assistantEntry: null })
    chatPanelMounts.current = 0
  })

  it('shows the launcher when collapsed', () => {
    render(<AssistantDock />)
    // No @testing-library/jest-dom in this repo (see every other
    // *.test.tsx) — getByTestId already throws if the node is absent, so
    // toBeTruthy() (built into vitest's expect) confirms the query
    // succeeded, matching this codebase's convention.
    expect(screen.getByTestId('companion-compose')).toBeTruthy()
  })

  it('keeps ChatPanel mounted while collapsed', () => {
    render(<AssistantDock />)
    expect(screen.getByTestId('chat-panel-stub')).toBeTruthy()
  })

  it('expands when the launcher is clicked', async () => {
    const user = userEvent.setup()
    render(<AssistantDock />)
    await user.click(screen.getByTestId('companion-compose'))
    expect(useUIStore.getState().assistantDockOpen).toBe(true)
    expect(useUIStore.getState().assistantEntry).toBe('compose')
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

    await user.click(screen.getByTestId('companion-compose'))
    expect(useUIStore.getState().assistantDockOpen).toBe(true)
    expect(chatPanelMounts.current).toBe(1)

    await user.click(screen.getByTestId('assistant-dock-collapse'))
    expect(useUIStore.getState().assistantDockOpen).toBe(false)
    expect(chatPanelMounts.current).toBe(1)
  })
})

// ── Prominence (reported from the built app) ────────────────────────────────
//
// "I do not see the prominent button for the assistant when the app is
// launched." The dock shipped default-collapsed to a 40px strip holding a
// single 16px muted icon — which is the opt-in panel the spec exists to
// replace, wearing a different shape. The spec's collapsed state is the
// floating companion (compose, speak, Live, profile chip) on every surface.

describe('assistant dock prominence', () => {
  beforeEach(() => {
    localStorage.clear()
    useUIStore.setState({ assistantDockOpen: false, assistantEntry: null })
  })

  it('offers reviewed dictation from the collapsed companion', async () => {
    const user = userEvent.setup()
    render(<AssistantDock />)
    expect(screen.getByTestId('companion-speak')).toBeTruthy()
    await user.click(screen.getByTestId('companion-speak'))
    expect(useUIStore.getState().assistantDockOpen).toBe(true)
    expect(useUIStore.getState().assistantEntry).toBe('speak')
  })

  it('does not open the console when Live is clicked', async () => {
    const user = userEvent.setup()
    render(<AssistantDock />)
    await user.click(screen.getByTestId('companion-live'))
    expect(useUIStore.getState().assistantDockOpen).toBe(false)
    expect(useUIStore.getState().assistantEntry).toBeNull()
    expect(screen.getByTestId('companion-launcher').getAttribute('data-presence')).toBe('listening')
    expect(screen.getByTestId('companion-live-input')).toBeTruthy()
  })

  it('walks the results tabs from the live composer and leaves the console closed', async () => {
    const user = userEvent.setup()
    const seen: string[] = []
    const request = useUIStore.getState().requestResultsTab
    useUIStore.setState({
      activeSlidePanel: null,
      resultsTabRequest: null,
      uiMode: 'guided',
      requestResultsTab: (tab: string) => {
        seen.push(tab)
        request(tab)
      },
    })
    const view = render(<AssistantDock />)
    await user.click(screen.getByTestId('companion-live'))
    await user.type(
      screen.getByTestId('companion-live-input'),
      'Walk me through the key results in each tab',
    )
    await user.click(screen.getByTestId('companion-live-send'))
    expect(useUIStore.getState().assistantDockOpen).toBe(false)
    expect(useUIStore.getState().activeSlidePanel).toBe('results')
    expect(useUIStore.getState().uiMode).toBe('expert')
    expect(seen).toEqual(['overview'])
    expect(screen.getByTestId('companion-live-caption').textContent).toContain('Tab 1 of')
    expect(screen.getByTestId('companion-live-caption').textContent).toContain('Overview')
    view.unmount()
  })

  it('reserves no column width while collapsed', () => {
    render(<AssistantDock />)
    const dock = screen.getByTestId('assistant-dock')
    const classes = dock.className.split(/\s+/)
    expect(classes).toContain('w-0')
    expect(classes).not.toContain('w-10')
    expect(dock.style.width).toBe('')
  })

  it('hides the floating companion while the console is open', () => {
    useUIStore.setState({ assistantDockOpen: true })
    render(<AssistantDock />)
    expect(screen.queryByTestId('companion-launcher')).toBeNull()
    const handle = screen.getByTestId('assistant-dock-resize')
    expect(handle.getAttribute('role')).toBe('separator')
    expect(handle.getAttribute('aria-orientation')).toBe('vertical')
  })

  it('offers a resize handle when open', () => {
    useUIStore.setState({ assistantDockOpen: true })
    render(<AssistantDock />)
    const handle = screen.getByTestId('assistant-dock-resize')
    expect(handle.getAttribute('role')).toBe('separator')
    expect(handle.getAttribute('aria-orientation')).toBe('vertical')
  })

  it('renders at the stored width, not a hardcoded one', () => {
    useUIStore.setState({ assistantDockOpen: true, assistantDockWidth: 560 })
    render(<AssistantDock />)
    const dock = screen.getByTestId('assistant-dock')
    expect(dock.style.width).toBe('560px')
  })
})
