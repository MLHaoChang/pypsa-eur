/**
 * Issue 11 — ChatPanel opens GET …/progress when a tool_result reports
 * detached_progress, and feeds tool_progress into the chat store.
 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'
import { createChatProgressStream, createChatStream, getChatHistory } from '../api/chat'
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
    createChatProgressStream: vi.fn(() => () => {}),
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

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getChatHistory).mockResolvedValue({
    turns: [], last_session_id: null, bound_project: null,
    history_gap: 0, pending_turn: null,
  })
  useUIStore.setState({
    currentProject: 'Demo', activeSlidePanel: null, assistantDockOpen: true,
    projectMismatch: null,
  })
  useChatStore.setState({
    sessionId: 'sess-detached', pending: null, messages: [], error: null,
    profileId: null, streaming: false, streamCleanup: null, toolProgress: {},
  })
})

afterEach(() => cleanup())

it('opens a progress observer when a tool_result reports detached_progress', async () => {
  let onProgress: ((frame: { event: string; data: Record<string, unknown> }) => void) | null = null
  vi.mocked(createChatProgressStream).mockImplementation((_sid, onFrame) => {
    onProgress = onFrame
    return () => { onProgress = null }
  })
  vi.mocked(createChatStream).mockImplementation((_req, onFrame) => {
    queueMicrotask(() => {
      onFrame({
        event: 'session_init',
        data: { session_id: 'sess-detached', model: 'claude', turn_id: 't1' },
      })
      onFrame({
        event: 'tool_result',
        data: {
          tool_name: 'run_simulation',
          tool_use_id: 'tu-sim',
          result: { status: 'started', detached_progress: true },
        },
      })
      onFrame({ event: 'turn_done', data: {} })
      onFrame({ event: 'session_done', data: { reason: 'completed' } })
    })
    return () => {}
  })

  renderPanel()
  await screen.findByTestId('chat-input')
  // Drive a send so createChatStream runs (or inject via mock call).
  // The panel already has sessionId; fire the stream by taking a queued request.
  act(() => {
    useChatStore.getState().sendRequest('solve please', { source: 'hub-card' })
  })
  await waitFor(() => expect(vi.mocked(createChatStream)).toHaveBeenCalled())
  await waitFor(() => expect(vi.mocked(createChatProgressStream)).toHaveBeenCalled())
  expect(vi.mocked(createChatProgressStream).mock.calls[0][0]).toBe('sess-detached')

  act(() => {
    onProgress?.({
      event: 'tool_progress',
      data: { tool_use_id: 'tu-sim', kind: 'PHASE', line: 'Optimising' },
    })
  })
  expect(useChatStore.getState().toolProgress['tu-sim']).toEqual(
    expect.arrayContaining([expect.objectContaining({ kind: 'PHASE', line: 'Optimising' })]),
  )
})
