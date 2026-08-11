import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'
import { createChatStream } from '../api/chat'
import ChatPanel from './ChatPanel'

// That the composer actually SENDS the context.
//
// uiContext.test.ts proves the builder is right; that says nothing about
// whether anything calls it. This is the wiring, and it is the whole feature:
// a correct builder nobody invokes leaves "why is this so high?" exactly as
// broken as it was.
//
// The `input_mode` assertion is the smaller half. The spec makes it a field
// rather than an inference — "speech reciprocity depends on it and
// reconstructing it later from timing or content is guesswork" — so it has to
// be carried from the composer now, even though the spoken-reply half is not
// built yet. Getting it flowing while the send path is open is what stops
// that later work from having to reopen this file.

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

async function send(text: string) {
  const input = await screen.findByTestId('chat-input')
  fireEvent.change(input, { target: { value: text } })
  fireEvent.click(screen.getByTestId('chat-send'))
}

function lastRequest() {
  const calls = vi.mocked(createChatStream).mock.calls
  return calls[calls.length - 1][0]
}

beforeEach(() => {
  vi.clearAllMocks()
  useUIStore.setState({
    currentProject: 'Demo',
    activeSlidePanel: 'results',
    canvasView: 'blank',
    selectedComponent: { type: 'Generator', name: 'Onshore Wind 3' },
    compareRailOpen: false,
    resultsSnapshotIdx: 0,
  })
  useChatStore.setState({
    sessionId: 'sess-1', pending: null, messages: [], error: null,
    streaming: false, streamCleanup: null,
    usage: {
      input_tokens: 0, output_tokens: 0,
      cache_read_tokens: 0, cache_create_tokens: 0,
    },
  })
})

afterEach(() => cleanup())

it('sends what the user is looking at alongside the question', async () => {
  renderPanel()
  await send('why is this so high?')

  await waitFor(() => expect(vi.mocked(createChatStream)).toHaveBeenCalled())
  expect(lastRequest().ui_context).toEqual({
    panel: 'results',
    canvas_view: 'blank',
    selected_component: { class: 'Generator', name: 'Onshore Wind 3' },
  })
})

it('marks a typed turn as typed', async () => {
  renderPanel()
  await send('hello')

  await waitFor(() => expect(vi.mocked(createChatStream)).toHaveBeenCalled())
  expect(lastRequest().input_mode).toBe('text')
})

// Captured at SEND, not at mount. The user opens Results, selects a
// generator, and only then asks — a context frozen at mount would describe
// the screen they had before they went looking.
it('captures the screen as it is at send, not as it was at mount', async () => {
  useUIStore.setState({ activeSlidePanel: null, selectedComponent: null })
  renderPanel()

  useUIStore.setState({
    activeSlidePanel: 'results',
    selectedComponent: { type: 'Line', name: 'DC Link 2' },
  })
  await send('explain this')

  await waitFor(() => expect(vi.mocked(createChatStream)).toHaveBeenCalled())
  expect(lastRequest().ui_context?.selected_component).toEqual({
    class: 'Line', name: 'DC Link 2',
  })
})

it('omits the field entirely when there is nothing to say', async () => {
  useUIStore.setState({
    activeSlidePanel: null, selectedComponent: null,
    canvasView: 'blank', compareRailOpen: false, resultsSnapshotIdx: 0,
  })
  renderPanel()
  await send('hello')

  await waitFor(() => expect(vi.mocked(createChatStream)).toHaveBeenCalled())
  expect(lastRequest().ui_context).toBeUndefined()
})
