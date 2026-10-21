import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'
import ChatPanel from './ChatPanel'

// LC-12: when a chat plan's job ends, the panel continues the conversation by
// itself, once. Nobody typed it, so the transcript shows a muted marker, never
// a user bubble; it waits while the user is typing.

vi.mock('../api/chat', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/chat')>()
  return {
    ...actual,
    createChatStream: vi.fn(() => () => {}),
    getChatHistory: vi.fn().mockResolvedValue({
      turns: [], last_session_id: null, bound_project: null,
      history_gap: 0, pending_turn: null,
    }),
    listContinuations: vi.fn().mockResolvedValue([]),
    dismissContinuation: vi.fn().mockResolvedValue(true),
    postChatAbort: vi.fn(),
    postChatConfirm: vi.fn(),
    getApiKeySettings: vi.fn().mockResolvedValue({
      configured: true, source: 'env', hint: 'abcd',
      overridden_by_environment: false, storage_path: '/tmp/user.env',
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

const due = { job_id: 'fg:0123abcd', status: 'completed', task_id: 't-1',
  message: 'job fg:0123abcd finished: completed (plan: Raise L1 and L2)' }

beforeEach(async () => {
  const chat = await import('../api/chat')
  vi.mocked(chat.listContinuations).mockResolvedValue([])
  useUIStore.setState({ currentProject: 'Demo', activeSlidePanel: null })
  useChatStore.setState({
    sessionId: 'sess-1', pending: null, messages: [], error: null,
    streaming: false, streamCleanup: null,
    usage: {
      input_tokens: 0, output_tokens: 0,
      cache_read_tokens: 0, cache_create_tokens: 0, reported: true,
    },
  })
})

afterEach(() => { cleanup(); vi.clearAllMocks() })

it('sends a due continuation with no message and shows a marker, not a user bubble', async () => {
  const chat = await import('../api/chat')
  vi.mocked(chat.listContinuations).mockResolvedValue([due])
  renderPanel()
  await vi.waitFor(() => expect(chat.createChatStream).toHaveBeenCalledTimes(1))
  const body = vi.mocked(chat.createChatStream).mock.calls[0][0]
  expect(body.continuation_job_id).toBe(due.job_id)
  expect(body).not.toHaveProperty('message')
  const shown = useChatStore.getState().messages
  expect(shown.map((m) => [m.role, m.content])).toEqual([['system', 'The solve finished · continuing']])
  expect(screen.queryByText(/fg:0123abcd/)).toBeNull()
})

it('holds the continuation while the composer has text', async () => {
  const chat = await import('../api/chat')
  const user = (await import('@testing-library/user-event')).default.setup()
  renderPanel()
  const box = await screen.findByRole('textbox') as HTMLTextAreaElement
  await user.type(box, 'half a thought')
  vi.mocked(chat.listContinuations).mockResolvedValue([due])
  // A new session id re-checks at once (the panel's reopen path).
  act(() => { useChatStore.setState({ sessionId: 'sess-2' }) })
  await vi.waitFor(() => expect(chat.listContinuations).toHaveBeenCalledWith('sess-2'))
  await act(async () => { await new Promise((r) => setTimeout(r, 0)) })
  expect(chat.createChatStream).not.toHaveBeenCalled()
  expect(box.value).toBe('half a thought')
})

it('replays a continuation turn from history as the marker', async () => {
  const chat = await import('../api/chat')
  useChatStore.setState({ messages: [] })
  vi.mocked(chat.getChatHistory).mockResolvedValue({
    turns: [{ user: due.message, assistant: [{ type: 'text', text: 'The re-solve finished.' }], ts: 1 }],
    last_session_id: 'sess-1', bound_project: 'Demo', history_gap: 0, pending_turn: null,
  } as never)
  renderPanel()
  await vi.waitFor(() => expect(useChatStore.getState().messages.length).toBeGreaterThan(0))
  const [first] = useChatStore.getState().messages
  expect([first.role, first.content]).toEqual(['system', 'The solve finished · continuing'])
})
