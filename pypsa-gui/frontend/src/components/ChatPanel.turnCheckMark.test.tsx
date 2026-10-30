/** The per-turn checking mark that `session_init.assurance` drives. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'
import { createChatStream, getChatHistory } from '../api/chat'
import * as speechOut from '../utils/speechOut'
import ChatPanel from './ChatPanel'

const speech: { onFinal?: (text: string) => void } = {}
vi.mock('../api/reliability', () => ({
  CHECKED_ANSWERS_QUERY_KEY: ['chat', 'checked-answer-capabilities'],
  getCheckedAnswerCapabilities: vi.fn().mockResolvedValue({ version: 1, mode: 'disabled',
    profiles: {} }),
}))
vi.mock('../hooks/useSpeechToText', () => ({
  useSpeechToText: (options: { onFinal?: (text: string) => void }) => {
    speech.onFinal = options.onFinal
    return { available: true, supported: true, listening: false, interim: '',
      permissionDenied: false, toggle: vi.fn(), stop: vi.fn() }
  },
}))
vi.mock('../api/dictation', () => ({
  getDictationConfig: vi.fn().mockResolvedValue({ available: false, model: '',
    languages: ['en'], max_bytes: 10485760, max_recording_seconds: 120 }),
}))
vi.mock('../api/chat', async importOriginal => ({
  ...await importOriginal<typeof import('../api/chat')>(),
  createChatStream: vi.fn(() => () => {}),
  getChatHistory: vi.fn(), postChatAbort: vi.fn(), postChatConfirm: vi.fn(),
  getApiKeySettings: vi.fn().mockResolvedValue({ configured: true, source: 'env', hint: 'test',
    overridden_by_environment: false, storage_path: '/tmp/synthetic' }),
}))
vi.mock('../api/uploads', () => ({
  deleteUpload: vi.fn(), getUploadBlobUrl: vi.fn(),
  listUploads: vi.fn().mockResolvedValue([]), uploadFile: vi.fn(),
  UploadError: class UploadError extends Error {},
}))
vi.mock('../api/network', () => ({ networkApi: {
  getMeta: vi.fn().mockResolvedValue({ name: 'Demo', bus_count: 1, snapshot_count: 2 }),
} }))
vi.mock('../api/simulation', () => ({ simulationApi: {
  getStatus: vi.fn().mockResolvedValue({ running: false, status: 'idle', dispatch: 'none' }),
} }))

function emit(event: string, data: Record<string, unknown>) {
  const calls = vi.mocked(createChatStream).mock.calls
  act(() => calls[calls.length - 1][1]({ event, data }))
}
async function turn(assurance: unknown) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><ChatPanel /></QueryClientProvider>)
  fireEvent.change(await screen.findByTestId('chat-input'), { target: { value: 'Read the selected results.' } })
  fireEvent.click(screen.getByTestId('chat-send'))
  await waitFor(() => expect(vi.mocked(createChatStream)).toHaveBeenCalled())
  emit('session_init', { session_id: 'mark-session', model: 'synthetic', turn_id: 'turn-1',
    ...(assurance === undefined ? {} : { assurance }) })
}

describe('per-turn checking mark', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getChatHistory).mockResolvedValue({ turns: [], last_session_id: null,
      bound_project: null, history_gap: 0, pending_turn: null })
    useUIStore.setState({ currentProject: 'Demo', activeSlidePanel: null,
      selectedComponent: null, assistantDockOpen: true })
    useChatStore.setState({ sessionId: 'mark-session', pending: null, messages: [], error: null,
      profileId: null, streaming: false, streamCleanup: null,
      usage: { input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_create_tokens: 0, reported: true } })
  })
  afterEach(() => { cleanup(); vi.restoreAllMocks() })

  it('marks an unchecked turn quietly', async () => {
    await turn({ version: 1, mode: 'legacy', state: 'unvalidated' })
    const mark = await screen.findByTestId('chat-turn-check-unchecked')
    expect(mark.textContent).toBe('Not checked')
    expect(mark.className).not.toMatch(/amber|red|accent/)
    expect(screen.queryByTestId('chat-turn-check-checking')).toBeNull()
  })

  it.each(['strict', 'pilot'])('marks a %s turn as being checked', async mode => {
    await turn({ version: 1, mode, state: 'checking' })
    expect((await screen.findByTestId('chat-turn-check-checking')).textContent).toBe('Answer will be checked')
    expect(screen.queryByTestId('chat-turn-check-unchecked')).toBeNull()
  })

  it.each([undefined, null, 'x', { version: 2, mode: 'strict', state: 'checking' },
    { version: 1, mode: 'legacy', state: 'checking' }, { version: 1, mode: 'strict', state: 'unvalidated' }])(
    'renders nothing for a missing or malformed assurance field (%j)', async assurance => {
      await turn(assurance)
      await screen.findByText('Read the selected results.')
      expect(screen.queryByTestId('chat-turn-check-unchecked')).toBeNull()
      expect(screen.queryByTestId('chat-turn-check-checking')).toBeNull()
    })
})
