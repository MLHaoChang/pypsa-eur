import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'
import { createChatStream } from '../api/chat'
import * as speechOut from '../utils/speechOut'
import ChatPanel from './ChatPanel'

// Modal reciprocity.
//
//   "A turn begun with the microphone is answered aloud; a typed turn is
//    answered in text. Plus a global mute. This matches how people already
//    expect assistants to behave and requires no settings trip: the spoken
//    mode is chosen by the act of using the microphone."
//
// The negative cases carry more weight than the positive one. An assistant
// that talks when you typed is not a feature with a rough edge — it is the
// thing that gets the whole assistant muted permanently, which is the same
// argument the spec makes for keeping the launch greeting silent: "An app that
// talks at you unprompted on every launch is the fastest way to get the
// assistant switched off for good."

const speechOpts: { onFinal?: (t: string) => void } = {}

vi.mock('../hooks/useSpeechToText', () => ({
  useSpeechToText: (opts: { onFinal?: (t: string) => void }) => {
    speechOpts.onFinal = opts.onFinal
    return {
      available: true, supported: true, listening: false, interim: '',
      permissionDenied: false, toggle: vi.fn(), stop: vi.fn(),
    }
  },
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

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ChatPanel />
    </QueryClientProvider>,
  )
}

/** The onFrame callback ChatPanel handed to the (mocked) stream opener. */
function frameSink() {
  const calls = vi.mocked(createChatStream).mock.calls
  return calls[calls.length - 1][1]
}

async function sendTyped(text: string) {
  const input = await screen.findByTestId('chat-input')
  fireEvent.change(input, { target: { value: text } })
  fireEvent.click(screen.getByTestId('chat-send'))
}

async function sendDictated(text: string) {
  await screen.findByTestId('chat-input')
  act(() => { speechOpts.onFinal?.(text) })
  fireEvent.click(screen.getByTestId('chat-send'))
}

function answer(text: string) {
  act(() => {
    frameSink()({ event: 'token', data: { delta: text } })
    frameSink()({ event: 'turn_done', data: {} })
  })
}

let speakSpy: ReturnType<typeof vi.spyOn>
let cancelSpy: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  vi.clearAllMocks()
  // jsdom has no speech synthesis, and the mute control deliberately hides
  // itself where the platform has none — a dead toggle is worse than no
  // toggle. So the environment has to claim it exists before the control can
  // be under test at all.
  vi.stubGlobal('speechSynthesis', { speak: vi.fn(), cancel: vi.fn(), speaking: false })
  vi.stubGlobal('SpeechSynthesisUtterance', class { constructor(public text: string) {} })
  speakSpy = vi.spyOn(speechOut, 'speak').mockImplementation(() => {})
  cancelSpy = vi.spyOn(speechOut, 'cancelSpeech').mockImplementation(() => {})
  useUIStore.setState({
    currentProject: 'Demo', activeSlidePanel: null,
    selectedComponent: null, assistantSpeakEnabled: true,
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

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('answers a dictated turn aloud', async () => {
  renderPanel()
  await sendDictated('what is the objective?')
  await waitFor(() => expect(vi.mocked(createChatStream)).toHaveBeenCalled())

  answer('The solve is optimal at 1.2 billion euro.')

  expect(speakSpy).toHaveBeenCalled()
  expect(String(speakSpy.mock.calls[0][0])).toContain('optimal')
})

// Regression. `input_mode` was read from `dictatedRef` inside the object
// literal handed to createChatStream — which is evaluated AFTER
// `dispatchSend` resets the composer and clears that flag, so every turn was
// reported as typed. The deixis suite could not catch it: its only
// input_mode test sends a TYPED turn and asserts 'text', which is the value a
// permanently-broken flag also produces.
it('reports a dictated turn as voice on the wire', async () => {
  renderPanel()
  await sendDictated('what is the objective?')

  await waitFor(() => expect(vi.mocked(createChatStream)).toHaveBeenCalled())
  const calls = vi.mocked(createChatStream).mock.calls
  expect(calls[calls.length - 1][0].input_mode).toBe('voice')
})

it('stays silent for a typed turn', async () => {
  renderPanel()
  await sendTyped('what is the objective?')
  await waitFor(() => expect(vi.mocked(createChatStream)).toHaveBeenCalled())

  answer('The solve is optimal at 1.2 billion euro.')

  expect(speakSpy).not.toHaveBeenCalled()
})

it('stays silent when the user has muted it', async () => {
  useUIStore.setState({ assistantSpeakEnabled: false })
  renderPanel()
  await sendDictated('what is the objective?')
  await waitFor(() => expect(vi.mocked(createChatStream)).toHaveBeenCalled())

  answer('The solve is optimal at 1.2 billion euro.')

  expect(speakSpy).not.toHaveBeenCalled()
})

// The spec is explicit: "The launch greeting is silent, deliberately. An app
// that talks at you unprompted on every launch is the fastest way to get the
// assistant switched off for good."
it('says nothing on mount', async () => {
  renderPanel()
  await screen.findByTestId('chat-launch-greeting')
  expect(speakSpy).not.toHaveBeenCalled()
})

// Markdown is what the model actually emits. Speaking it raw is the failure
// this reduction exists to prevent.
it('speaks the prose, not the markdown', async () => {
  renderPanel()
  await sendDictated('summarise')
  await waitFor(() => expect(vi.mocked(createChatStream)).toHaveBeenCalled())

  answer('**Onshore Wind 3** produces 42 MW')

  const spokenText = String(speakSpy.mock.calls[0][0])
  expect(spokenText).not.toContain('**')
  expect(spokenText).toContain('Onshore Wind 3')
})

it('stops talking when the user aborts', async () => {
  renderPanel()
  await sendDictated('summarise')
  await waitFor(() => expect(vi.mocked(createChatStream)).toHaveBeenCalled())

  fireEvent.click(screen.getByTestId('chat-abort'))

  expect(cancelSpy).toHaveBeenCalled()
})

it('offers a mute control that persists the choice', async () => {
  renderPanel()
  const toggle = await screen.findByTestId('chat-speak-toggle')

  fireEvent.click(toggle)
  expect(useUIStore.getState().assistantSpeakEnabled).toBe(false)
  // Persisted, because a preference about whether a machine talks out loud is
  // exactly the kind a user expects to set once.
  expect(localStorage.getItem('network-diagram:assistant-speak')).toBe('off')

  fireEvent.click(toggle)
  expect(useUIStore.getState().assistantSpeakEnabled).toBe(true)
})
