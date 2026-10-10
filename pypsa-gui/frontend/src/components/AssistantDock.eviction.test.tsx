import { describe, it, expect, beforeEach, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { useEffect } from 'react'
import { useUIStore } from '../store/uiStore'
import { slidePanelClickShouldClose } from '../utils/slidePanelDismiss'

vi.mock('./ChatPanel', () => ({
  default: () => <div data-testid="chat-panel-stub">chat</div>,
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

// The reported bug, as a guard rail.
//
// "It switches to the results panel, but the chat disappears, so I don't know
// what the chatbot wants to say." The assistant used to be the `'chat'` member
// of `SlidePanel`, and `activeSlidePanel` holds ONE value — so the assistant
// was mutually exclusive with every view it exists to explain, and
// `applyUiNavigate` answering the agent's own `ui_open_panel` by calling
// `setSlidePanel('results')` closed the assistant in the act of obeying.
//
// The dock does not read `activeSlidePanel` at all, and that independence IS
// the fix — so this passes the moment AssistantDock exists rather than failing
// first. Its value is forward-looking: it fails against any future change that
// reconnects the two (gating the dock on `!activeSlidePanel`, moving it back
// inside the panel container, re-adding a `'chat'` member).
describe('the assistant survives its own navigation', () => {
  beforeEach(() => {
    localStorage.clear()
    useUIStore.setState({ assistantDockOpen: true, activeSlidePanel: null })
  })

  it('stays mounted and open when a ui_event navigates to a full-screen tab', () => {
    render(<AssistantDock />)
    // No @testing-library/jest-dom in this repo — getByTestId already throws
    // when the node is absent, so toBeTruthy() (a vitest built-in) is this
    // codebase's idiom for presence. Same convention as AssistantDock.test.tsx.
    expect(screen.getByTestId('chat-panel-stub')).toBeTruthy()

    // Exactly what applyUiNavigate does on `ui_open_panel` → results.
    // `results` is a member of App.tsx's FULL_SCREEN_TABS, the case that used
    // to take over the whole main area.
    act(() => {
      useUIStore.getState().setSlidePanel('results')
    })

    expect(useUIStore.getState().assistantDockOpen).toBe(true)
    expect(screen.getByTestId('chat-panel-stub')).toBeTruthy()
    // Visibility here is CSS-class-driven and jsdom loads no stylesheet, so a
    // getComputedStyle check (toBeVisible) would report "visible" regardless
    // of the `hidden` class. The className-token check is the working
    // assertion — same convention as CommandPalette.test.tsx and
    // AssistantDock.test.tsx. Split on whitespace rather than matching a
    // substring so the unrelated `overflow-hidden` utility can't false-match.
    expect(screen.getByTestId('assistant-dock-body').className.split(/\s+/)).not.toContain('hidden')
  })

  // The same coexistence requirement, running the other direction.
  //
  // App.tsx's click-outside-to-close effect (the `onPointerDown` handler
  // mounted while a panel is open) closes the active slide panel on any
  // mousedown outside the panel, the Sidebar (<aside>), or an element marked
  // `data-no-panel-close`. The dock is a plain div outside all three,
  // so without the marker, clicking the composer to ask a follow-up question
  // about Results would close Results. That was structurally impossible while
  // the assistant WAS the slide panel; making it a sibling is what put it in
  // reach, so the marker is part of this change and not decoration.
  //
  // Asserted on the attribute rather than by driving App's listener because
  // the listener lives in App.tsx, which pulls the whole workbench tree
  // (canvas, react-query, auth) into a test about one attribute. The
  // attribute is the entire contract between the two.
  it('is exempt from click-outside-to-close so a click in the composer keeps the panel open', () => {
    render(<AssistantDock />)
    const dock = screen.getByTestId('assistant-dock')
    expect(dock.hasAttribute('data-no-panel-close')).toBe(true)
    // The body is a descendant, so `closest()` — which is what App.tsx calls —
    // finds the marker from anything the user can actually click.
    expect(screen.getByTestId('assistant-dock-body').closest('[data-no-panel-close]')).toBe(dock)
  })
})

/** App.tsx's capture-phase listener, using the same dismiss rule. */
function PanelCloseHarness() {
  const activeSlidePanel = useUIStore((s) => s.activeSlidePanel)
  const setSlidePanel = useUIStore((s) => s.setSlidePanel)
  useEffect(() => {
    if (!activeSlidePanel) return
    const onPointerDown = (e: MouseEvent) => {
      if (slidePanelClickShouldClose(e.target, null)) setSlidePanel(null)
    }
    document.addEventListener('mousedown', onPointerDown, true)
    return () => document.removeEventListener('mousedown', onPointerDown, true)
  }, [activeSlidePanel, setSlidePanel])
  return <AssistantDock />
}

describe('the floating companion survives click-outside-to-close', () => {
  beforeEach(() => {
    localStorage.clear()
    useUIStore.setState({ assistantDockOpen: false, activeSlidePanel: 'results' })
  })

  const companionClickIds = [
    'companion-compose',
    'companion-speak',
    'companion-live',
    'companion-profile',
  ] as const

  it.each(companionClickIds)(
    'keeps the slide panel open when %s is clicked',
    (testId) => {
      render(<PanelCloseHarness />)
      const companion = screen.getByTestId('companion-launcher')
      expect(companion.hasAttribute('data-no-panel-close')).toBe(true)
      const target = screen.getByTestId(testId)
      expect(target.closest('[data-no-panel-close]')).toBe(companion)
      fireEvent.mouseDown(target, { bubbles: true })
      expect(useUIStore.getState().activeSlidePanel).toBe('results')
    },
  )
})
