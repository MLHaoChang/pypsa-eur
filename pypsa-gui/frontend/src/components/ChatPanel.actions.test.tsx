import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'
import { createChatStream, postChatRewind } from '../api/chat'
import ChatPanel from './ChatPanel'

// Per-message actions: copy, retry, edit-and-resend.
//
// None of these existed. A turn that failed, or landed wrong, or answered a
// question the user had phrased badly, could only be dealt with by retyping
// it — and the panel is the one surface in the app where the user's input is
// long-form prose.
//
// The load-bearing test is `rewinds the server history before retrying`.
// `session.messages` is the array replayed to the model on every turn and it
// lives on the SERVER, so a retry that only clears the screen re-asks the
// question with the previous answer still two messages above it in context.
// The model reads its own last answer and repeats it, and the user concludes
// the button does nothing. Everything else here is affordances; that one is
// whether "retry" is true.

vi.mock('../api/chat', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/chat')>()
  return {
    ...actual,
    createChatStream: vi.fn(() => () => {}),
    getChatHistory: vi.fn().mockResolvedValue({
      turns: [], last_session_id: null, bound_project: null,
      history_gap: 0, pending_turn: null,
    }),
    postChatAbort: vi.fn(),
    postChatConfirm: vi.fn(),
    postChatRewind: vi.fn().mockResolvedValue({ ok: true, dropped: 2 }),
    getApiKeySettings: vi.fn().mockResolvedValue({
      configured: true, source: 'env', hint: 'abcd',
      overridden_by_environment: false, storage_path: '/tmp/user.env',
    }),
    putApiKeySettings: vi.fn(),
    deleteApiKeySettings: vi.fn(),
  }
})
vi.mock('../api/uploads', () => ({
  deleteUpload: vi.fn(), getUploadBlobUrl: vi.fn(),
  listUploads: vi.fn().mockResolvedValue([]), uploadFile: vi.fn(),
  UploadError: class UploadError extends Error {},
}))
vi.mock('../api/network', () => ({
  networkApi: {
    getMeta: vi.fn().mockResolvedValue({ name: 'Demo', bus_count: 9, snapshot_count: 24 }),
  },
}))
vi.mock('../api/simulation', () => ({
  simulationApi: {
    getStatus: vi.fn().mockResolvedValue({
      running: false, status: 'completed', condition: 'optimal',
      objective: 1, solve_time: 1, dispatch: 'fresh',
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

/** A finished exchange: a question, and an answer that used a tool. */
function seedExchange() {
  useChatStore.setState({
    messages: [
      { id: 'u1', role: 'user', content: 'why is Onshore Wind 3 curtailed?', ts: 1 },
      { id: 't1', role: 'tool', content: 'get_meta()', tool_use_id: 'tu-1', ts: 2 },
      { id: 'a1', role: 'assistant', content: 'It hits its **p_max_pu** ceiling.', ts: 3 },
    ] as never,
  })
}

const writeText = vi.fn().mockResolvedValue(undefined)

beforeEach(() => {
  vi.clearAllMocks()
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText }, configurable: true, writable: true,
  })
  useUIStore.setState({ currentProject: 'Demo', activeSlidePanel: null, selectedComponent: null })
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

// ── copy ────────────────────────────────────────────────────────────────────

it('copies an assistant answer as its raw markdown', async () => {
  renderPanel()
  act(() => { seedExchange() })

  fireEvent.click(await screen.findByTestId('chat-copy-a1'))

  await waitFor(() => expect(writeText).toHaveBeenCalled())
  // The MARKDOWN, not the rendered text. What people do with a copied answer
  // is paste it somewhere that renders markdown — a PR description, a doc,
  // an issue — and the rendered form arrives there as flattened prose with
  // the table gone.
  expect(writeText.mock.calls[0][0]).toBe('It hits its **p_max_pu** ceiling.')
})

// A tool row is a one-line synthetic summary of a call, not content anyone
// wants on their clipboard.
it('offers no copy on tool rows', async () => {
  renderPanel()
  act(() => { seedExchange() })
  await screen.findByTestId('chat-copy-a1')

  expect(screen.queryByTestId('chat-copy-t1')).toBeNull()
})

// ── retry ───────────────────────────────────────────────────────────────────

it('rewinds the server history before retrying', async () => {
  renderPanel()
  act(() => { seedExchange() })

  fireEvent.click(await screen.findByTestId('chat-retry-a1'))

  await waitFor(() => expect(vi.mocked(postChatRewind)).toHaveBeenCalled())
  expect(vi.mocked(postChatRewind).mock.calls[0]).toEqual(['sess-1', 1])
  // And only THEN re-sends, or the rewind races the turn it is clearing for.
  await waitFor(() => expect(vi.mocked(createChatStream)).toHaveBeenCalled())
  const order = vi.mocked(postChatRewind).mock.invocationCallOrder[0]
  expect(order).toBeLessThan(vi.mocked(createChatStream).mock.invocationCallOrder[0])
})

it('re-sends the original question verbatim', async () => {
  renderPanel()
  act(() => { seedExchange() })

  fireEvent.click(await screen.findByTestId('chat-retry-a1'))

  await waitFor(() => expect(vi.mocked(createChatStream)).toHaveBeenCalled())
  expect(vi.mocked(createChatStream).mock.calls[0][0].message)
    .toBe('why is Onshore Wind 3 curtailed?')
})

it('clears the discarded answer off screen', async () => {
  renderPanel()
  act(() => { seedExchange() })

  fireEvent.click(await screen.findByTestId('chat-retry-a1'))

  await waitFor(() => {
    const msgs = useChatStore.getState().messages
    // The question survives — it is being re-asked, not withdrawn. Its answer
    // and the tool rows that produced it do not.
    expect(msgs.map(m => m.role)).toEqual(['user'])
  })
})

// ── edit and resend ─────────────────────────────────────────────────────────

it('puts a question back in the composer to edit', async () => {
  renderPanel()
  act(() => { seedExchange() })

  fireEvent.click(await screen.findByTestId('chat-edit-u1'))

  // Awaited, because the composer is filled only AFTER the server rewind
  // lands. Filling it first would be snappier and wrong: a rewind that fails
  // leaves the old turn on screen with its text also sitting in the composer,
  // so the user sends a duplicate into a history that still holds the
  // original.
  const input = await waitFor(() => {
    const el = screen.getByTestId('chat-input') as HTMLTextAreaElement
    expect(el.value).not.toBe('')
    return el
  })
  expect(input.value).toBe('why is Onshore Wind 3 curtailed?')
  // Nothing is sent yet — the point is to change it first.
  expect(vi.mocked(createChatStream)).not.toHaveBeenCalled()
})

it('withdraws the edited turn from screen and server', async () => {
  renderPanel()
  act(() => { seedExchange() })

  fireEvent.click(await screen.findByTestId('chat-edit-u1'))

  await waitFor(() => expect(vi.mocked(postChatRewind)).toHaveBeenCalled())
  // The whole turn goes, question included — the user is replacing it, so
  // leaving the old phrasing on screen above the new one would show them
  // asking twice.
  expect(useChatStore.getState().messages).toEqual([])
})

// ── while streaming ─────────────────────────────────────────────────────────

it('offers no retry or edit while a turn is running', async () => {
  renderPanel()
  act(() => { seedExchange(); useChatStore.setState({ streaming: true }) })
  await screen.findByTestId('chat-copy-a1')

  // rewind_session refuses under `_turn_in_flight`, so these would silently
  // do half of what they claim: clear the screen, leave the server history.
  expect(screen.queryByTestId('chat-retry-a1')).toBeNull()
  expect(screen.queryByTestId('chat-edit-u1')).toBeNull()
  // Copy is still fine — it touches nothing.
  expect(screen.getByTestId('chat-copy-a1')).toBeTruthy()
})
