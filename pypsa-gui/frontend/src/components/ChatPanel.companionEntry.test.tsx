import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'
import { createChatStream } from '../api/chat'
import ChatPanel from './ChatPanel'
import { getDictationConfig, transcribeRecording } from '../api/dictation'
import { installRecordingMocks } from '../utils/audioRecorder.testHelpers'

vi.mock('../api/dictation', () => ({ getDictationConfig: vi.fn(), transcribeRecording: vi.fn() }))

vi.mock('../hooks/useSpeechToText', () => ({
  useSpeechToText: () => ({
    available: true, supported: true, listening: false, interim: '',
    permissionDenied: false, toggle: vi.fn(), stop: vi.fn(),
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

import { getChatProfiles } from '../api/llmSettings'

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ChatPanel />
    </QueryClientProvider>,
  )
}

const recording = { current: null as ReturnType<typeof installRecordingMocks> | null }

beforeEach(() => {
  recording.current = installRecordingMocks()
  useUIStore.setState({
    currentProject: 'Demo', assistantDockOpen: false, assistantEntry: null, activeSlidePanel: null,
  })
  useChatStore.setState({ messages: [], profileId: null, boundProfileId: null })
  vi.clearAllMocks()
  vi.mocked(getDictationConfig).mockResolvedValue({ available: true, model: 'gpt-4o-mini-transcribe', languages: ['en', 'de', 'zh'], max_bytes: 10485760, max_recording_seconds: 120 })
  vi.mocked(transcribeRecording).mockResolvedValue('')
})

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('compose opens the typed composer and leaves dictation closed', async () => {
  renderPanel()
  act(() => {
    useUIStore.setState({ assistantDockOpen: true, assistantEntry: 'compose' })
  })
  await waitFor(() => {
    expect(document.activeElement).toBe(screen.getByTestId('chat-input'))
  })
  expect(screen.queryByTestId('dictation-panel')).toBeNull()
  expect(recording.current!.getUserMedia).not.toHaveBeenCalled()
  expect(useUIStore.getState().assistantEntry).toBeNull()
})

it('speak opens the review panel and does not start a recording or a chat turn', async () => {
  renderPanel()
  act(() => {
    useUIStore.setState({ assistantDockOpen: true, assistantEntry: 'speak' })
  })
  expect(await screen.findByTestId('dictation-panel')).toBeTruthy()
  expect(recording.current!.getUserMedia).not.toHaveBeenCalled()
  expect(vi.mocked(createChatStream)).not.toHaveBeenCalled()
  expect(useUIStore.getState().assistantEntry).toBeNull()
})

it('profiles focuses the existing model select and does not start dictation', async () => {
  renderPanel()
  act(() => {
    useUIStore.setState({ assistantDockOpen: true, assistantEntry: 'profiles' })
  })
  await waitFor(() => {
    expect(screen.getByTestId('chat-model-select').getAttribute('data-profiles-state')).toBe('ready')
  })
  await waitFor(() => {
    expect(document.activeElement).toBe(screen.getByTestId('chat-model-select'))
  })
  expect(screen.queryByTestId('dictation-panel')).toBeNull()
  expect(recording.current!.getUserMedia).not.toHaveBeenCalled()
  expect(useUIStore.getState().assistantEntry).toBeNull()
})

it('profiles clears the entry when the profile list failed to load', async () => {
  vi.mocked(getChatProfiles).mockRejectedValueOnce(new Error('profiles unavailable'))
  renderPanel()
  act(() => {
    useUIStore.setState({ assistantDockOpen: true, assistantEntry: 'profiles' })
  })
  await waitFor(() => {
    expect(screen.getByTestId('chat-model-select').getAttribute('data-profiles-state')).toBe('error')
  })
  await waitFor(() => {
    expect(useUIStore.getState().assistantEntry).toBeNull()
  })
  act(() => {
    useUIStore.setState({ assistantDockOpen: false })
  })
  act(() => {
    useUIStore.setState({ assistantDockOpen: true, assistantEntry: 'compose' })
  })
  await waitFor(() => {
    expect(document.activeElement).toBe(screen.getByTestId('chat-input'))
  })
})

it('profiles clears the entry when no profiles are configured', async () => {
  vi.mocked(getChatProfiles).mockResolvedValueOnce({
    profiles: [],
    active_profile_id: null,
  })
  renderPanel()
  act(() => {
    useUIStore.setState({ assistantDockOpen: true, assistantEntry: 'profiles' })
  })
  await waitFor(() => {
    expect(screen.getByTestId('chat-model-select').getAttribute('data-profiles-state')).toBe('empty')
  })
  await waitFor(() => {
    expect(useUIStore.getState().assistantEntry).toBeNull()
  })
  act(() => {
    useUIStore.setState({ assistantDockOpen: false })
  })
  act(() => {
    useUIStore.setState({ assistantDockOpen: true, assistantEntry: 'compose' })
  })
  await waitFor(() => {
    expect(document.activeElement).toBe(screen.getByTestId('chat-input'))
  })
})

it('drops a pending entry when the dock is closed', async () => {
  renderPanel()
  act(() => {
    useUIStore.setState({ assistantDockOpen: false, assistantEntry: 'profiles' })
  })
  await waitFor(() => {
    expect(useUIStore.getState().assistantEntry).toBeNull()
  })
})
