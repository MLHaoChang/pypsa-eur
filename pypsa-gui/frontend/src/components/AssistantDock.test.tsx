import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { useEffect } from 'react'
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'

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

const { ensureLiveDurableThread } = vi.hoisted(() => ({
  ensureLiveDurableThread: vi.fn(() => Promise.resolve({ thread_id: 't-live', generation: 1 })),
}))

vi.mock('../api/durableConversations', () => ({
  ensureLiveDurableThread,
}))

import AssistantDock from './AssistantDock'

describe('AssistantDock', () => {
  beforeEach(() => {
    localStorage.clear()
    useUIStore.setState({ assistantDockOpen: false, assistantEntry: null })
    useChatStore.setState({
      pending: null, durableThreadId: null, utteranceGeneration: 1,
      streaming: false, messages: [], choice: null, error: null, requestQueue: [],
    })
    chatPanelMounts.current = 0
    ensureLiveDurableThread.mockClear()
  })

  afterEach(() => {
    vi.useRealTimers()
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
  it('shows the pending confirmation beside the companion while the console is closed', () => {
    useUIStore.setState({ assistantDockOpen: false })
    useChatStore.setState({
      pending: {
        tool_use_id: 'tu1',
        tool_name: 'update_component',
        args: { name: 'bus-a' },
        safety_tier: 'write',
        confirmation_token: 'tok',
        ttl_seconds: 60,
        expires_at_epoch_ms: Date.now() + 60_000,
      },
    })
    render(<AssistantDock />)
    const card = screen.getByTestId('companion-confirmation')
    expect(card.textContent).toContain('update_component')
    expect(screen.getByTestId('assistant-dock-body').className.split(/\s+/)).toContain('hidden')
    expect(screen.getAllByTestId('companion-confirmation')).toHaveLength(1)
  })

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
    useChatStore.setState({
      pending: null, durableThreadId: null, utteranceGeneration: 1,
      streaming: false, messages: [], choice: null, error: null, requestQueue: [],
    })
    ensureLiveDurableThread.mockClear()
  })

  it('offers reviewed dictation from the collapsed companion', async () => {
    const user = userEvent.setup()
    render(<AssistantDock />)
    expect(screen.getByTestId('companion-speak')).toBeTruthy()
    await user.click(screen.getByTestId('companion-speak'))
    expect(useUIStore.getState().assistantDockOpen).toBe(true)
    expect(useUIStore.getState().assistantEntry).toBe('speak')
  })

  it('opens in-page text with truthful labels and focus without opening the console', async () => {
    const user = userEvent.setup()
    render(<AssistantDock />)
    await user.click(screen.getByTestId('companion-live'))
    expect(useUIStore.getState().assistantDockOpen).toBe(false)
    expect(useUIStore.getState().assistantEntry).toBeNull()
    expect(screen.getByTestId('companion-launcher').getAttribute('data-presence')).toBe('idle')
    expect(screen.getByTestId('companion-launcher').getAttribute('data-media-state')).toBe('off')
    expect(screen.getByTestId('companion-live').textContent).toBe('Chat here')
    expect(screen.getByRole('button', { name: 'Start voice' }).hasAttribute('disabled')).toBe(true)
    expect(screen.getByText(/Continuous voice is not connected/)).toBeTruthy()
    expect(screen.getByTestId('companion-live-input')).toBeTruthy()
    expect(document.activeElement).toBe(screen.getByTestId('companion-live-input'))
  })

  it('opens the console on the profile list from the profile chip', async () => {
    const user = userEvent.setup()
    render(<AssistantDock />)
    await user.click(screen.getByTestId('companion-profile'))
    expect(useUIStore.getState().assistantDockOpen).toBe(true)
    expect(useUIStore.getState().assistantEntry).toBe('profiles')
  })

  it('does not start the microphone, a recording or a media session when Live is clicked', async () => {
    const getUserMedia = vi.fn()
    const fetchSpy = vi.fn(() => Promise.reject(new Error('no network in this test')))
    vi.stubGlobal('fetch', fetchSpy)
    vi.stubGlobal('MediaRecorder', vi.fn())
    vi.stubGlobal('RTCPeerConnection', vi.fn())
    Object.defineProperty(navigator, 'mediaDevices', { value: { getUserMedia }, configurable: true })
    try {
      const user = userEvent.setup()
      render(<AssistantDock />)
      await user.click(screen.getByTestId('companion-live'))
      expect(getUserMedia).not.toHaveBeenCalled()
      expect(MediaRecorder).not.toHaveBeenCalled()
      expect(RTCPeerConnection).not.toHaveBeenCalled()
      expect(fetchSpy).not.toHaveBeenCalled()
      expect(screen.queryByTestId('dictation-panel')).toBeNull()
      expect(useUIStore.getState().assistantDockOpen).toBe(false)
    } finally {
      vi.unstubAllGlobals()
      Reflect.deleteProperty(navigator, 'mediaDevices')
    }
  })

  it('binds a durable conversation when Live opens', async () => {
    const user = userEvent.setup()
    render(<AssistantDock />)
    await user.click(screen.getByTestId('companion-live'))
    expect(ensureLiveDurableThread).toHaveBeenCalled()
  })

  it('sends a live sentence other than the walk through the chat stream and leaves the console closed', async () => {
    const user = userEvent.setup()
    const heard: string[] = []
    const onUtterance = (ev: Event) => {
      heard.push((ev as CustomEvent<string>).detail)
    }
    window.addEventListener('companion:live-utterance', onUtterance)
    render(<AssistantDock />)
    await user.click(screen.getByTestId('companion-live'))
    await user.type(screen.getByTestId('companion-live-input'), 'List the generators')
    await user.click(screen.getByTestId('companion-live-send'))
    expect(useUIStore.getState().assistantDockOpen).toBe(false)
    expect(heard).toEqual(['List the generators'])
    // The ChatPanel stub does not start a stream; submitting text alone
    // cannot claim execution or playback.
    expect(screen.getByTestId('companion-launcher').getAttribute('data-presence')).toBe('idle')
    expect(screen.getByTestId('companion-live-echo').textContent).toBe('List the generators')
    expect(screen.queryByTestId('companion-live-caption')).toBeNull()
    expect(screen.queryByTestId('companion-outcome')).toBeNull()
    window.removeEventListener('companion:live-utterance', onUtterance)
  })

  it('reflects actual response state and never fabricates playback after stream completion', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    vi.useFakeTimers({ shouldAdvanceTime: true })
    render(<AssistantDock />)
    await user.click(screen.getByTestId('companion-live'))
    await user.type(screen.getByTestId('companion-live-input'), 'List the generators')
    await user.click(screen.getByTestId('companion-live-send'))
    const launcher = () => screen.getByTestId('companion-launcher')
    await act(async () => { useChatStore.setState({ streaming: true }) })
    expect(launcher().getAttribute('data-presence')).toBe('thinking')
    await act(async () => {
      useChatStore.setState({
        streaming: false,
        messages: [
          { id: 'u1', role: 'user', content: 'List the generators', ts: 1 },
          { id: 'a1', role: 'assistant', content: 'There is one generator.', ts: 2 },
        ],
      })
    })
    expect(launcher().getAttribute('data-presence')).toBe('idle')
    expect(launcher().getAttribute('data-media-state')).toBe('off')
    expect(screen.getByTestId('companion-work-status').textContent).toBe('Ready for a message')
    // Canonical text is also readable beside the collapsed companion.
    expect(screen.getByText('There is one generator.')).toBeTruthy()
    expect(screen.getByTestId('companion-outcome')).toBeTruthy()
    await act(async () => { vi.advanceTimersByTime(5_000) })
    expect(screen.queryByTestId('companion-live-echo')).toBeNull()
    expect(launcher().getAttribute('data-presence')).toBe('idle')
    expect(launcher().getAttribute('data-media-state')).toBe('off')
  })

  it('keeps the download chip for a Live turn that produced a file', async () => {
    const user = userEvent.setup()
    render(<AssistantDock />)
    await user.click(screen.getByTestId('companion-live'))
    await act(async () => {
      useChatStore.setState({
        messages: [
          { id: 'u1', role: 'user', content: 'Export the chart', ts: 1 },
          { id: 'a1', role: 'assistant', content: 'The chart is ready.', ts: 2 },
          {
            id: 't1', role: 'tool', content: '→ build_delivery', ts: 3,
            tool_result: { download_url: '/api/projects/Demo/uploads/0123456789abcdef/blob', filename: 'chart.png' },
          },
        ],
      })
    })
    expect(screen.getByTestId('companion-download').getAttribute('download')).toBe('chart.png')
    expect(screen.getByText('The chart is ready.')).toBeTruthy()
  })

  it('shows response work independently of whether in-page chat is open', async () => {
    render(<AssistantDock />)
    await act(async () => { useChatStore.setState({ streaming: true }) })
    expect(screen.getByTestId('companion-launcher').getAttribute('data-presence')).toBe('thinking')
    expect(screen.getByTestId('companion-launcher').getAttribute('data-media-state')).toBe('off')
    expect(screen.getByTestId('companion-work-status').textContent).toBe('Response in progress')
    await act(async () => { useChatStore.setState({ streaming: false }) })
    expect(screen.getByTestId('companion-launcher').getAttribute('data-presence')).toBe('idle')
  })

  it('queues a live sentence while the assistant is already streaming without opening the console', async () => {
    const user = userEvent.setup()
    useChatStore.setState({ streaming: true, requestQueue: [] })
    const heard: string[] = []
    const onUtterance = (ev: Event) => {
      heard.push((ev as CustomEvent<string>).detail)
    }
    window.addEventListener('companion:live-utterance', onUtterance)
    render(<AssistantDock />)
    await user.click(screen.getByTestId('companion-live'))
    await user.type(screen.getByTestId('companion-live-input'), 'List the generators')
    await user.click(screen.getByTestId('companion-live-send'))
    expect(heard).toEqual([])
    const queue = useChatStore.getState().requestQueue
    expect(queue).toHaveLength(1)
    expect(queue[0]).toMatchObject({ text: 'List the generators', source: 'live' })
    expect(useUIStore.getState().assistantDockOpen).toBe(false)
    expect(screen.getByTestId('companion-live-caption').textContent).toContain(
      'Queued — will send when the assistant is free.',
    )
    window.removeEventListener('companion:live-utterance', onUtterance)
    useChatStore.setState({ streaming: false, requestQueue: [] })
  })

  it('keeps only the latest live correction while busy', async () => {
    const user = userEvent.setup()
    useChatStore.setState({ streaming: true, requestQueue: [] })
    render(<AssistantDock />)
    await user.click(screen.getByTestId('companion-live'))
    await user.type(screen.getByTestId('companion-live-input'), 'First correction')
    await user.click(screen.getByTestId('companion-live-send'))
    await user.clear(screen.getByTestId('companion-live-input'))
    await user.type(screen.getByTestId('companion-live-input'), 'Latest correction')
    await user.click(screen.getByTestId('companion-live-send'))
    const queue = useChatStore.getState().requestQueue
    expect(queue).toHaveLength(1)
    expect(queue[0].text).toBe('Latest correction')
    expect(useUIStore.getState().assistantDockOpen).toBe(false)
    useChatStore.setState({ streaming: false, requestQueue: [] })
  })

  it('does not allow an old binding failure to change a reopened chat caption', async () => {
    let rejectBind!: (error: Error) => void
    ensureLiveDurableThread.mockImplementationOnce(() => new Promise((_, reject) => { rejectBind = reject }))
    const user = userEvent.setup()
    render(<AssistantDock />)
    await user.click(screen.getByRole('button', { name: 'Chat here' }))
    await user.click(screen.getByRole('button', { name: 'Close chat here' }))
    await user.click(screen.getByRole('button', { name: 'Chat here' }))
    await act(async () => rejectBind(new Error('late bind failure')))
    expect(screen.getByTestId('companion-live-caption').textContent).toBe(
      'Type a message here, or open reviewed dictation in the assistant.',
    )
  })

  it('keeps Guided policy and does not forward a local walkthrough to a competing model controller', async () => {
    const user = userEvent.setup()
    const forwarded = vi.fn()
    window.addEventListener('companion:live-utterance', forwarded)
    useUIStore.setState({ currentProject: 'Demo', discussedRun: null,
      activeSlidePanel: null, resultsTabRequest: null, uiMode: 'guided' })
    const view = render(<AssistantDock />)
    try {
      await user.click(screen.getByTestId('companion-live'))
      await user.type(screen.getByTestId('companion-live-input'), 'Walk me through the key results in each tab')
      await user.click(screen.getByTestId('companion-live-send'))
      expect(useUIStore.getState().assistantDockOpen).toBe(false)
      expect(useUIStore.getState().uiMode).toBe('guided')
      expect(useUIStore.getState().resultsTabRequest).toBeNull()
      expect(screen.getByTestId('companion-live-caption').textContent).toContain('Guided mode')
      expect(forwarded).not.toHaveBeenCalled()
    } finally {
      view.unmount()
      window.removeEventListener('companion:live-utterance', forwarded)
    }
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
