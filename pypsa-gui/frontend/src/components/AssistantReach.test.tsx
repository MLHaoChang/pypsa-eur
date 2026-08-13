import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'
import ChatPanel from './ChatPanel'

// Reaching the assistant without a mouse.
//
// The spec's headline is an assistant that is "present when the tool
// launches". It ends up being the only major surface in the app you cannot
// reach from the keyboard: `toggleAssistantDock` had exactly one caller, the
// sidebar button. Meanwhile the app has Cmd-K for the palette, Cmd-P for
// projects, `[` for the sidebar, `?` for help, and V / C for canvas modes.
//
// And opening it left the caret nowhere. Click Assistant, then click again in
// the composer — two actions for one intent, which also undercuts the
// shortcut before it is built: a shortcut that opens a panel you then have to
// reach for with the mouse has saved nothing.
//
// The focus rule has a sharp edge worth stating: focus follows the OPENING,
// never the mount. The dock now defaults to open, so focusing on mount would
// steal the caret on every page load — from the project search, from a form
// the user was mid-way through, from the canvas.

vi.mock('../api/chat', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/chat')>()
  return {
    ...actual,
    createChatStream: vi.fn(() => () => {}),
    getChatHistory: vi.fn().mockResolvedValue({
      turns: [], last_session_id: null, bound_project: null,
      history_gap: 0, pending_turn: null,
    }),
    postChatAbort: vi.fn(), postChatConfirm: vi.fn(), postChatRewind: vi.fn(),
    getApiKeySettings: vi.fn().mockResolvedValue({
      configured: true, source: 'env', hint: 'abcd',
      overridden_by_environment: false, storage_path: '/tmp/user.env',
    }),
    putApiKeySettings: vi.fn(), deleteApiKeySettings: vi.fn(),
  }
})
vi.mock('../api/uploads', () => ({
  deleteUpload: vi.fn(), getUploadBlobUrl: vi.fn(),
  listUploads: vi.fn().mockResolvedValue([]), uploadFile: vi.fn(),
  UploadError: class UploadError extends Error {},
}))
vi.mock('../api/network', () => ({
  networkApi: { getMeta: vi.fn().mockResolvedValue({ name: 'Demo', bus_count: 9, snapshot_count: 24 }) },
}))
vi.mock('../api/simulation', () => ({
  simulationApi: {
    getStatus: vi.fn().mockResolvedValue({
      running: false, status: 'idle', condition: null,
      objective: null, solve_time: null, dispatch: 'none',
    }),
  },
}))

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ChatPanel />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo', assistantDockOpen: true })
  useChatStore.setState({
    sessionId: 'sess-1', pending: null, messages: [], error: null,
    streaming: false, streamCleanup: null,
    usage: {
      input_tokens: 0, output_tokens: 0,
      cache_read_tokens: 0, cache_create_tokens: 0,
    },
  })
})

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('focus follows the opening', () => {
  it('puts the caret in the composer when the dock opens', async () => {
    useUIStore.setState({ assistantDockOpen: false })
    renderPanel()
    const input = await screen.findByTestId('chat-input')
    expect(document.activeElement).not.toBe(input)

    act(() => { useUIStore.getState().setAssistantDockOpen(true) })

    await waitFor(() => expect(document.activeElement).toBe(input))
  })

  // The one that keeps this from becoming an annoyance. The dock defaults to
  // open, so a mount-triggered focus would steal the caret on every load.
  it('does not steal the caret on mount', async () => {
    renderPanel()
    const input = await screen.findByTestId('chat-input')

    // Give any effect a chance to fire before asserting the negative.
    await waitFor(() => expect(screen.getByTestId('chat-panel')).toBeTruthy())
    expect(document.activeElement).not.toBe(input)
  })

  it('does not fight for the caret when the dock closes', async () => {
    renderPanel()
    act(() => { useUIStore.getState().setAssistantDockOpen(false) })

    const input = screen.getByTestId('chat-input')
    expect(document.activeElement).not.toBe(input)
  })
})

describe('the keyboard shortcut', () => {
  it('opens the assistant and focuses it', async () => {
    useUIStore.setState({ assistantDockOpen: false })
    renderPanel()
    await screen.findByTestId('chat-input')

    act(() => {
      fireEvent.keyDown(document.body, { key: 'j', metaKey: true })
    })

    expect(useUIStore.getState().assistantDockOpen).toBe(true)
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByTestId('chat-input')))
  })

  it('closes it again on a second press', async () => {
    renderPanel()
    await screen.findByTestId('chat-input')

    act(() => { fireEvent.keyDown(document.body, { key: 'j', metaKey: true }) })

    expect(useUIStore.getState().assistantDockOpen).toBe(false)
  })

  // Typing a literal "j" into any field must not toggle a panel. The modifier
  // check has to come first, exactly as App.tsx's palette handler documents.
  it('ignores an unmodified j', async () => {
    useUIStore.setState({ assistantDockOpen: false })
    renderPanel()
    await screen.findByTestId('chat-input')

    act(() => { fireEvent.keyDown(document.body, { key: 'j' }) })

    expect(useUIStore.getState().assistantDockOpen).toBe(false)
  })

  // Unlike the palette's Cmd-K, this one MUST work while the caret is in a
  // text field — the composer is a text field, and "close the assistant I am
  // typing in" is the most natural moment to press it.
  it('works from inside the composer', async () => {
    renderPanel()
    const input = await screen.findByTestId('chat-input')

    act(() => { fireEvent.keyDown(input, { key: 'j', metaKey: true }) })

    expect(useUIStore.getState().assistantDockOpen).toBe(false)
  })
})
