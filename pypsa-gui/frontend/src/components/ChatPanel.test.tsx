import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, cleanup, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'
import {
  createChatStream,
  getChatHistory,
  postChatConfirm,
  putApiKeySettings,
  type ChatFrame,
} from '../api/chat'
import ChatPanel from './ChatPanel'

vi.mock('../api/chat', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/chat')>()
  return {
    ...actual,
    createChatStream: vi.fn(),
    getChatHistory: vi.fn().mockResolvedValue({ turns: [], last_session_id: null, bound_project: null }),
    postChatAbort: vi.fn(),
    postChatConfirm: vi.fn().mockResolvedValue({ ok: true }),
    getApiKeySettings: vi.fn().mockResolvedValue({
      configured: false,
      source: null,
      hint: null,
      overridden_by_environment: false,
      storage_path: '/tmp/user.env',
    }),
    putApiKeySettings: vi.fn(),
    deleteApiKeySettings: vi.fn(),
  }
})

vi.mock('../api/uploads', () => ({
  deleteUpload: vi.fn(),
  getUploadBlobUrl: vi.fn(),
  listUploads: vi.fn().mockResolvedValue([]),
  uploadFile: vi.fn(),
  UploadError: class UploadError extends Error {},
}))

afterEach(() => cleanup())

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo', activeSlidePanel: null, assistantDockOpen: false })
  // `streaming` and `streamCleanup` have to be reset too: `onSend` returns
  // early while `streaming` is true, so a test that leaves it set makes every
  // later test's send a silent no-op — which reads as "the component ignored
  // my click" rather than as pollution.
  useChatStore.setState({
    sessionId: null, pending: null, messages: [],
    streaming: false, streamCleanup: null,
  })
  vi.mocked(postChatConfirm).mockClear()
  // `createChatStream` accumulates across every `it()` in this file — each one
  // sends at least once — so an assertion on its call COUNT is otherwise
  // order-dependent. `mockClear` resets calls only; the per-test
  // `mockImplementation` in `sendAndScript` is unaffected.
  vi.mocked(createChatStream).mockClear()
})

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ChatPanel />
    </QueryClientProvider>,
  )
}

async function sendAndScript(scriptedFrames: ChatFrame[]) {
  vi.mocked(createChatStream).mockImplementation((_req, onFrame) => {
    for (const f of scriptedFrames) onFrame(f)
    return () => {}
  })
  const user = userEvent.setup()
  await user.type(screen.getByTestId('chat-input'), 'hello')
  await user.click(screen.getByTestId('chat-send'))
}

// U-1 — "API key missing" must not be a dead end.
//
// This is the state the PACKAGED app was permanently in: the bundle ships no
// `backend/.env` by design, so this banner was the user's entire experience of
// the assistant, with nothing anywhere to act on. The banner now carries the
// setup form itself. Asserting it here rather than only in ApiKeySetup.test.tsx
// is the point — the component passing its own tests while never being rendered
// would leave U-1 exactly as broken as before.
it('offers the API key setup form when the backend reports missing_api_key', async () => {
  renderPanel()
  await sendAndScript([
    { event: 'session_init', data: { session_id: 'sess-nokey' } },
    {
      event: 'error',
      data: { error_kind: 'missing_api_key', message: 'ANTHROPIC_API_KEY is not set' },
    },
  ])
  expect(await screen.findByText('API key missing')).toBeTruthy()
  expect(await screen.findByTestId('chat-api-key-input')).toBeTruthy()
})

it('clears the missing-key banner once a key is saved', async () => {
  // The save has to be legible. A red "API key missing" box still on screen
  // underneath a form that just reported success tells the user it did not
  // work, and they have no other signal to go on.
  vi.mocked(putApiKeySettings).mockResolvedValue({
    configured: true,
    source: 'settings',
    hint: '…wxyz',
    overridden_by_environment: false,
    storage_path: '/tmp/user.env',
  })
  renderPanel()
  await sendAndScript([
    { event: 'session_init', data: { session_id: 'sess-save' } },
    { event: 'error', data: { error_kind: 'missing_api_key', message: 'not set' } },
  ])

  const user = userEvent.setup()
  await user.type(await screen.findByTestId('chat-api-key-input'), 'sk-ant-typed')
  await user.click(screen.getByTestId('chat-api-key-save'))

  await waitFor(() => expect(screen.queryByTestId('chat-error-banner')).toBeNull())
})

it('does not offer the setup form for an unrelated error', async () => {
  // The negative control: a form that rendered under every error kind would
  // pass the test above while being wrong everywhere else.
  renderPanel()
  await sendAndScript([
    { event: 'session_init', data: { session_id: 'sess-rate' } },
    { event: 'error', data: { error_kind: 'rate_limited', message: 'slow down' } },
  ])
  expect(await screen.findByText('Rate limited')).toBeTruthy()
  expect(screen.queryByTestId('chat-api-key-input')).toBeNull()
})

// ── F2 — a hostile localStorage must not take the panel down ────────────────
//
// The panel reads four preference keys and writes six, and two of the reads run
// inside `useState` initializers — so an unguarded throw happens DURING render
// and React unwinds the whole subtree. The user loses the assistant, not a
// setting.
//
// Two distinct failure modes, because they need different guards:
//   * SecurityError on the PROPERTY — a browser set to block site data throws
//     on `window.localStorage` itself, before any method is reached. A guard
//     wrapped only around the call would not catch it.
//   * QuotaExceededError on `setItem` — Safari Private Browsing, or a full
//     origin quota. Reads succeed; only writes throw.
//
// The property descriptor is restored in a `finally` inside the test rather
// than an `afterEach`, because vitest.setup.ts's own `afterEach` calls
// `localStorage.clear()` and would itself throw against a hostile stand-in.
async function withHostileStorage(
  mode: 'throws-on-property-access' | 'throws-on-write',
  body: () => Promise<void> | void,
) {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')!
  const boom = () => {
    throw new DOMException('Access to storage is denied', 'SecurityError')
  }
  if (mode === 'throws-on-property-access') {
    Object.defineProperty(globalThis, 'localStorage', { get: boom, configurable: true })
  } else {
    Object.defineProperty(globalThis, 'localStorage', {
      value: {
        getItem: () => null,
        setItem: () => {
          throw new DOMException('exceeded the quota', 'QuotaExceededError')
        },
        removeItem: () => {},
        clear: () => {},
      },
      configurable: true,
      writable: true,
    })
  }
  try {
    await body()
  } finally {
    Object.defineProperty(globalThis, 'localStorage', original)
  }
}

it('still renders when reading window.localStorage throws SecurityError', async () => {
  await withHostileStorage('throws-on-property-access', async () => {
    renderPanel()
    // The prompt input is below both `useState` initializers that read a
    // preference, so its presence proves render completed rather than unwound.
    expect(await screen.findByTestId('chat-input')).toBeTruthy()
    expect(screen.getByTestId('chat-send')).toBeTruthy()
  })
})

it('still sends when persisting a preference throws QuotaExceededError', async () => {
  await withHostileStorage('throws-on-write', async () => {
    renderPanel()
    // Not just "it rendered": drive a real send. `onSend` reads
    // `chat:firstSendAck` and the mount effect writes `chat:promptHeight`, so a
    // throw on either path would surface here and not in the render-only test.
    await sendAndScript([
      { event: 'session_init', data: { session_id: 'sess-quota' } },
      { event: 'turn_done', data: {} },
    ])
    expect(vi.mocked(createChatStream)).toHaveBeenCalledTimes(1)
  })
})

it('renders tool_request and tool_result frames in the transcript', async () => {
  renderPanel()
  await sendAndScript([
    { event: 'session_init', data: { session_id: 'sess-1' } },
    { event: 'tool_request', data: { tool_name: 'list_projects', tool_use_id: 'tu1' } },
    { event: 'tool_result', data: { tool_name: 'list_projects', tool_use_id: 'tu1' } },
    { event: 'turn_done', data: {} },
  ])
  expect(await screen.findByText('→ list_projects')).toBeTruthy()
  expect(await screen.findByText('✓ list_projects')).toBeTruthy()
})

it('renders an approve/reject control for tool_pending_confirmation and calls postChatConfirm with confirmation_token on approve', async () => {
  renderPanel()
  await sendAndScript([
    { event: 'session_init', data: { session_id: 'sess-2' } },
    {
      event: 'tool_pending_confirmation',
      data: {
        tool_use_id: 'tu2',
        tool_name: 'delete_component',
        args: { component_class: 'Generator', name: 'SmokeSolar' },
        safety_tier: 'write',
        confirmation_token: 'tok-abc',
        ttl_seconds: 120,
      },
    },
  ])
  const card = await screen.findByTestId('chat-confirmation-card')
  expect(card).toBeTruthy()
  const approve = screen.getByTestId('chat-confirm-approve')
  // 'delete_component' is not in ChatPanel.tsx's TYPED_CONFIRMATION_TOOLS
  // set (only delete_project/save_project/save_project_as/
  // restore_project_snapshot/cascade_delete_bus require typed confirmation),
  // so Approve is enabled immediately with no typed input needed.
  expect((approve as HTMLButtonElement).disabled).toBe(false)
  const user = userEvent.setup()
  await user.click(approve)
  expect(postChatConfirm).toHaveBeenCalledWith('sess-2', {
    token: 'tok-abc',
    decision: 'approve',
  })
})

// ── An unmount mid-turn must not kill the turn ────────────────────────────
//
// Reported: "show a summary of the key results and the key graphs" switches
// the app to Results, and then either the conversation is gone or the chat is
// still streaming with no tokens on screen. Both came from one fact —
// ChatPanel's only mount used to be `activeSlidePanel === 'chat'`, so its own
// frame handler answering a `ui_event` with setSlidePanel('results') unmounted
// it mid-turn.
//
// That specific trigger is now structurally impossible: 'chat' is not a
// SlidePanel member and AssistantDock keeps ChatPanel mounted regardless of
// navigation (see AssistantDock.eviction.test.tsx, which guards it).
//
// These tests still guard the OTHER half of the contract, which navigation
// only happened to be the loudest trigger for: an unmount from any cause must
// not abort a live turn, tokens arriving while unmounted must still land, a
// remount must not clobber the in-flight turn with disk state, the terminal
// frame must still close the connection, and an idle stream must still be
// closed on unmount. Those paths remain reachable — an ErrorBoundary fallback
// after a render crash, a project switch, HMR — so `scriptNavigateMidTurn`
// still drives a real one: the ui_event frame it emits is exactly the frame
// the reported bug arrived on, and the explicit `unmount()` below now stands
// in for those remaining causes rather than being something navigation does
// by itself.

function scriptNavigateMidTurn() {
  const cleanup = vi.fn()
  let emit: ((f: ChatFrame) => void) | undefined
  vi.mocked(createChatStream).mockImplementation((_req, onFrame) => {
    emit = onFrame
    onFrame({ event: 'session_init', data: { session_id: 'sess-nav' } })
    onFrame({ event: 'token', data: { delta: 'Key results: ' } })
    // The agent opens Results. ChatPanel is what performs this.
    onFrame({ event: 'ui_event', data: { kind: 'navigate', panel_id: 'results' } })
    return cleanup
  })
  return { cleanup, emit: (f: ChatFrame) => emit?.(f) }
}

async function sendSummaryRequest() {
  const user = userEvent.setup()
  await user.type(screen.getByTestId('chat-input'), 'summarise the key results')
  await user.click(screen.getByTestId('chat-send'))
}

it('does not abort the live stream when the panel unmounts mid-turn', async () => {
  const { cleanup } = scriptNavigateMidTurn()
  const { unmount } = renderPanel()
  await sendSummaryRequest()

  // The agent's own directive opened Results. It no longer takes the
  // assistant down with it — the dock is outside everything activeSlidePanel
  // governs — so this is now just a check that the navigation happened.
  expect(useUIStore.getState().activeSlidePanel).toBe('results')
  // Stand-in for the unmount causes that DO remain: an ErrorBoundary
  // fallback, a project switch, HMR. Any of them can land mid-turn.
  unmount()

  expect(cleanup).not.toHaveBeenCalled()
})

it('keeps recording tokens that arrive after the panel is unmounted', async () => {
  // The stream's frame handler writes only to Zustand and react-query, never
  // to component state — so the turn can complete with the panel unmounted,
  // and does, as long as nobody closes the connection.
  const { emit } = scriptNavigateMidTurn()
  const { unmount } = renderPanel()
  await sendSummaryRequest()
  unmount()

  emit({ event: 'token', data: { delta: 'objective fell 12%.' } })

  const text = useChatStore.getState().messages.map((m) => m.content).join('')
  expect(text).toContain('objective fell 12%.')
})

it('does not wipe an in-flight turn when the panel is reopened', async () => {
  // Remount re-runs the chat.jsonl hydration, which calls setMessages() with
  // whatever is on disk. The turn still streaming has not been persisted yet,
  // so replaying disk over it erases what the user already watched arrive.
  //
  // The assertion has to happen AFTER that replay would have landed. A bare
  // `waitFor` on the message text passes on its first tick — before the
  // hydration promise resolves — so it reports success under the very bug it
  // is meant to catch. Wait for the fetch itself, then flush, then assert.
  scriptNavigateMidTurn()
  const { unmount } = renderPanel()
  await sendSummaryRequest()
  unmount()

  const callsBefore = vi.mocked(getChatHistory).mock.calls.length
  renderPanel()
  // Flush the mount effect and the promise chain the unguarded version would
  // have run — otherwise this asserts before the clobber could have happened
  // and passes under the bug.
  await act(async () => { await Promise.resolve(); await Promise.resolve() })

  // The guard short-circuits ahead of the fetch, so the re-read never happens.
  expect(vi.mocked(getChatHistory).mock.calls.length).toBe(callsBefore)
  const text = useChatStore.getState().messages.map((m) => m.content).join('')
  expect(text).toContain('Key results: ')
})

it('closes the stream when the turn ends, even with the panel unmounted', async () => {
  // The other half of the contract. Not aborting on unmount is only correct if
  // something else still closes the connection — otherwise every turn that
  // navigated away would leak an EventSource. The terminal frame owns it now.
  const cleanup = vi.fn()
  let emit: ((f: ChatFrame) => void) | undefined
  vi.mocked(createChatStream).mockImplementation((_req, onFrame) => {
    emit = onFrame
    onFrame({ event: 'session_init', data: { session_id: 'sess-end' } })
    onFrame({ event: 'ui_event', data: { kind: 'navigate', panel_id: 'results' } })
    return cleanup
  })
  const { unmount } = renderPanel()
  await sendSummaryRequest()
  unmount()
  expect(cleanup).not.toHaveBeenCalled()

  emit?.({ event: 'turn_done', data: {} })

  expect(cleanup).toHaveBeenCalledTimes(1)
  expect(useChatStore.getState().streaming).toBe(false)
})

it('still closes an idle stream when the panel unmounts', async () => {
  // Unmounting after the turn finished must not leave the connection open.
  const cleanup = vi.fn()
  vi.mocked(createChatStream).mockImplementation((_req, onFrame) => {
    onFrame({ event: 'session_init', data: { session_id: 'sess-idle' } })
    return cleanup
  })
  const { unmount } = renderPanel()
  await sendSummaryRequest()
  // Simulate an idle-but-open stream: streaming false, cleanup still set.
  useChatStore.setState({ streaming: false, streamCleanup: cleanup })

  unmount()

  expect(cleanup).toHaveBeenCalledTimes(1)
})

// ── AssistantDock expanding must re-run the stick-to-bottom autoscroll ─────
//
// While the dock is collapsed, ChatPanel sits under a `display:none`
// ancestor (kept mounted, not unmounted — see AssistantDock.tsx), so a
// scrollIntoView call made while collapsed lands on an element with no
// layout box and is a silent no-op in a real browser. jsdom does no layout
// at all — there is no way for any test to observe "the transcript ended up
// visually at the bottom" — so this only pins the narrower, still real
// precondition: the autoscroll effect has to re-run when the dock expands,
// so a scrollIntoView call happens at all once there's a box to scroll.
// The actual visual outcome (ask a question, collapse, let it stream,
// expand, see the latest token) needs a manual check in the built app.
it('re-fires the stick-to-bottom autoscroll when the assistant dock expands', async () => {
  renderPanel()
  await sendAndScript([
    { event: 'session_init', data: { session_id: 'sess-dock-scroll' } },
    { event: 'token', data: { delta: 'hello' } },
    { event: 'turn_done', data: {} },
  ])

  // Restored in a finally: this spy replaces vitest.setup.ts's own
  // `Element.prototype.scrollIntoView` no-op for the whole prototype, so
  // leaving it installed would silently hand the stand-in to every test
  // appended after this one in the file. Harmless today only because this is
  // currently last.
  const scrollSpy = vi.spyOn(Element.prototype, 'scrollIntoView')
  try {
    scrollSpy.mockClear()

    act(() => {
      useUIStore.setState({ assistantDockOpen: true })
    })

    expect(scrollSpy).toHaveBeenCalled()
  } finally {
    scrollSpy.mockRestore()
  }
})
