/**
 * Issue 02 — Live turns carry utterance_id + generation for shared admission.
 * Typed and hub-card sends omit both fields (pre-admission /stream behaviour).
 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'
import { createChatStream, getChatHistory } from '../api/chat'
import ChatPanel from './ChatPanel'

vi.mock('../hooks/useSpeechToText', () => ({
  useSpeechToText: () => ({
    available: false, supported: false, listening: false, interim: '',
    permissionDenied: false, toggle: vi.fn(), stop: vi.fn(),
  }),
}))
vi.mock('../api/dictation', () => ({
  getDictationConfig: vi.fn().mockResolvedValue({
    available: false, model: '', languages: ['en'],
    max_bytes: 10485760, max_recording_seconds: 120,
  }),
}))
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
      configured: true, source: 'env', hint: 'test',
      overridden_by_environment: false, storage_path: '/tmp/synthetic',
    }),
  }
})
vi.mock('../api/uploads', () => ({
  deleteUpload: vi.fn(), getUploadBlobUrl: vi.fn(),
  listUploads: vi.fn().mockResolvedValue([]), uploadFile: vi.fn(),
  UploadError: class UploadError extends Error {},
}))
vi.mock('../api/network', () => ({
  networkApi: {
    getMeta: vi.fn().mockResolvedValue({ name: 'Demo', bus_count: 1, snapshot_count: 2 }),
  },
}))
vi.mock('../api/simulation', () => ({
  simulationApi: {
    getStatus: vi.fn().mockResolvedValue({
      running: false, status: 'idle', dispatch: 'none',
    }),
  },
}))
vi.mock('../api/llmSettings', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/llmSettings')>()
  return {
    ...actual,
    getChatProfiles: vi.fn().mockResolvedValue({
      profiles: [{ id: 'claude', label: 'Claude', wire: 'anthropic', chat_ready: true }],
      active_profile_id: 'claude',
    }),
  }
})

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ChatPanel />
    </QueryClientProvider>,
  )
}

function lastStreamReq() {
  const calls = vi.mocked(createChatStream).mock.calls
  return calls[calls.length - 1]?.[0]
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getChatHistory).mockResolvedValue({
    turns: [], last_session_id: null, bound_project: null,
    history_gap: 0, pending_turn: null,
  })
  useUIStore.setState({
    currentProject: 'Demo', activeSlidePanel: null, assistantDockOpen: true,
    projectMismatch: false,
  })
  useChatStore.setState({
    sessionId: 'live-sess', pending: null, messages: [], error: null,
    profileId: null, streaming: false, streamCleanup: null,
    requestQueue: [], activeRequest: null, utteranceGeneration: 1,
  })
})

afterEach(() => cleanup())

it('idle Live utterance sends utterance_id and generation', async () => {
  renderPanel()
  await screen.findByTestId('chat-input')
  act(() => {
    window.dispatchEvent(new CustomEvent('companion:live-utterance', {
      detail: 'List the generators',
    }))
  })
  await waitFor(() => expect(vi.mocked(createChatStream)).toHaveBeenCalled())
  const req = lastStreamReq()
  expect(typeof req.utterance_id).toBe('string')
  expect(req.utterance_id!.length).toBeGreaterThan(8)
  expect(req.generation).toBe(1)
  expect(req.message).toBe('List the generators')
})

it('queued Live drain reuses the queue id as utterance_id', async () => {
  useChatStore.setState({
    streaming: false,
    requestQueue: [{
      id: 'utt-queued-42',
      text: 'Latest correction',
      source: 'live',
      queuedAt: Date.now(),
    }],
    utteranceGeneration: 3,
  })
  renderPanel()
  await waitFor(() => expect(vi.mocked(createChatStream)).toHaveBeenCalled())
  const req = lastStreamReq()
  expect(req.utterance_id).toBe('utt-queued-42')
  expect(req.generation).toBe(3)
  expect(req.message).toBe('Latest correction')
})

it('typed composer send omits utterance admission fields', async () => {
  renderPanel()
  const input = await screen.findByTestId('chat-input')
  fireEvent.change(input, { target: { value: 'hello from keyboard' } })
  fireEvent.click(screen.getByTestId('chat-send'))
  await waitFor(() => expect(vi.mocked(createChatStream)).toHaveBeenCalled())
  const req = lastStreamReq()
  expect(req.message).toBe('hello from keyboard')
  expect(req.utterance_id).toBeUndefined()
  expect(req.generation).toBeUndefined()
})

it('non-live card queue omits utterance admission fields', async () => {
  useChatStore.setState({
    streaming: false,
    requestQueue: [{
      id: 'card-1',
      text: 'Improve the battery',
      source: 'hub-card',
      queuedAt: Date.now(),
    }],
    utteranceGeneration: 2,
  })
  renderPanel()
  await waitFor(() => expect(vi.mocked(createChatStream)).toHaveBeenCalled())
  const req = lastStreamReq()
  expect(req.message).toBe('Improve the battery')
  expect(req.utterance_id).toBeUndefined()
  expect(req.generation).toBeUndefined()
})
