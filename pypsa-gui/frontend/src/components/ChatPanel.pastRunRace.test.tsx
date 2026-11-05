/**
 * LC-T10 run/generation races on a past run (LC-03 PR-P).
 *
 * A model turn opens a past run and its v2 acknowledgement waits for the
 * retained view. The real `acknowledgeMountedView` runs; only the network is
 * stubbed. The ACK posts once the retained view with that run's exact source
 * is on screen, and never after the conversation's generation moved on: a new
 * chat started mid-turn (the one path that changes only the generation), or
 * an approved answer whose check is still running.
 */
import { createHash, webcrypto } from 'node:crypto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'
import { createChatStream, getChatHistory } from '../api/chat'
import { client } from '../api/client'
import * as speechOut from '../utils/speechOut'
import * as answerAssurance from '../utils/answerAssurance'
import ChatPanel from './ChatPanel'

const speech: { onFinal?: (text: string) => void } = {}
vi.mock('../hooks/useSpeechToText', () => ({
  useSpeechToText: (options: { onFinal?: (text: string) => void }) => {
    speech.onFinal = options.onFinal
    return { available: true, supported: true, listening: false, interim: '',
      permissionDenied: false, toggle: vi.fn(), stop: vi.fn() }
  },
}))
vi.mock('../api/reliability', () => ({
  CHECKED_ANSWERS_QUERY_KEY: ['chat', 'checked-answer-capabilities'],
  getCheckedAnswerCapabilities: vi.fn().mockResolvedValue({ version: 1, mode: 'strict',
    profiles: { 'storage-unit-dispatch/1': { enabled: true } } }),
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

const PAST = { version: 1 as const, project_id: 'project-1', project_name: 'Demo',
  scope: 'historical' as const, data_sha256: 'a'.repeat(64), run_id: 'run-1',
  section: 'results_dispatch', window_from: 0, window_to: 23 }

function emit(event: string, data: Record<string, unknown>) {
  const calls = vi.mocked(createChatStream).mock.calls
  act(() => calls[calls.length - 1][1]({ event, data }))
}

function uiAcks() {
  return vi.mocked(client.post).mock.calls.filter(([url]) => url === '/chat/ui-ack')
}

/** The Results panel showing the retained view of `run`, as RunBoundView marks it. */
function showRetainedView(run = PAST) {
  const panel = document.createElement('div')
  panel.dataset.assurancePanel = 'results'
  panel.dataset.assuranceProject = 'Demo'
  const tab = document.createElement('div')
  tab.dataset.assuranceTab = 'dispatch'
  const view = document.createElement('section')
  view.dataset.resultTab = 'dispatch'
  view.dataset.resultBinding = JSON.stringify(run)
  tab.append(view)
  panel.append(tab)
  act(() => { document.body.append(panel) })
}

async function send(voice = false) {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <ChatPanel /></QueryClientProvider>)
  const input = await screen.findByTestId('chat-input')
  const question = 'Show the first run\'s dispatch.'
  if (voice) act(() => speech.onFinal?.(question))
  else fireEvent.change(input, { target: { value: question } })
  fireEvent.click(screen.getByTestId('chat-send'))
  await waitFor(() => expect(vi.mocked(createChatStream)).toHaveBeenCalled())
  return vi.mocked(createChatStream).mock.calls[0][0].binding_generation as number
}

/** One model turn that opens the past run; its ACK then waits for the view. */
async function openPastRun() {
  const generation = await send()
  emit('session_init', { session_id: 'race-session', turn_id: 't-1',
    assurance: { version: 1, mode: 'strict', state: 'checking' } })
  emit('tool_request', { tool_name: 'open_run_result', tool_use_id: 'tu-1' })
  emit('ui_event', { kind: 'open_run_result', panel_id: 'results', results_tab: 'dispatch',
    run_id: 'run-1', section: 'results_dispatch', status: 'completed', tool_use_id: 'tu-1',
    acknowledgement: { version: 2, action_id: 'a-1', turn_id: 't-1', binding_generation: generation,
      project_name: 'Demo', project_id: 'project-1', input_sha256: 'i'.repeat(64),
      target: { panel_id: 'results', results_tab: 'dispatch' }, source_binding: PAST } })
  expect(useUIStore.getState().discussedRun?.runId).toBe('run-1')
  expect(uiAcks()).toHaveLength(0)
}

const caption = () => useChatStore.getState().messages
  .find(m => m.role === 'tool' && m.tool_use_id === 'tu-1')?.content

describe('a past run on screen while the conversation moves on (LC-T10)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getChatHistory).mockResolvedValue({ turns: [], last_session_id: null,
      bound_project: null, history_gap: 0, pending_turn: null })
    vi.stubGlobal('crypto', webcrypto)
    vi.spyOn(client, 'post').mockResolvedValue({ data: { outcome: 'completed' } } as never)
    vi.spyOn(speechOut, 'speak').mockImplementation(() => {})
    vi.spyOn(speechOut, 'cancelSpeech').mockImplementation(() => {})
    useUIStore.setState({ currentProject: 'Demo', activeSlidePanel: null, discussedRun: null,
      resultsHighlight: null, assistantDockOpen: true, assistantSpeakEnabled: true })
    useChatStore.setState({ sessionId: 'race-session', pending: null, messages: [], error: null,
      profileId: null, streaming: false, streamCleanup: null })
  })
  afterEach(() => {
    cleanup()
    document.body.innerHTML = ''
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    useUIStore.setState({ discussedRun: null })
  })

  it('posts the ACK once the past run\'s retained view is shown, and captions it completed', async () => {
    await openPastRun()
    showRetainedView()
    await waitFor(() => expect(uiAcks()).toHaveLength(1))
    expect(uiAcks()[0][1]).toMatchObject({ action_id: 'a-1', source_binding: PAST })
    await waitFor(() => expect(caption()).toBe('✓ ui_open_panel (completed)'))
  })

  it('a new chat started mid-turn cancels the pending ACK: nothing posts, no completed caption', async () => {
    await openPastRun()
    act(() => useChatStore.getState().startNewChat())
    showRetainedView()
    await new Promise(resolve => setTimeout(resolve, 20))
    expect(uiAcks()).toHaveLength(0)
    expect(useChatStore.getState().messages.some(m => m.content.includes('(completed)'))).toBe(false)
  })

  it('the latest run\'s view on screen never certifies the past run\'s ACK', async () => {
    await openPastRun()
    // A newer solve finished and its current results are what a live tab marks.
    showRetainedView({ ...PAST, scope: 'current' as never, run_id: 'run-2', data_sha256: 'b'.repeat(64) })
    await new Promise(resolve => setTimeout(resolve, 20))
    expect(uiAcks()).toHaveLength(0)
    expect(caption()).not.toBe('✓ ui_open_panel (completed)')
  })

  async function approvedVoiceAnswer(text: string) {
    let finish: (matches: boolean) => void = () => {}
    vi.spyOn(answerAssurance, 'verifyApprovedText')
      .mockReturnValue(new Promise<boolean>(resolve => { finish = resolve }))
    await send(true)
    emit('session_init', { session_id: 'race-session', turn_id: 't-1', model: 'synthetic',
      assurance: { version: 1, mode: 'strict', state: 'checking' } })
    emit('answer_approved', { version: 1, answer_id: 'approved-1', turn_id: 't-1', verdict: 'limited',
      receipt: { policy_version: '1', evidence_ids: [], checked_block_ids: [], limitations: [],
        reviewer_status: 'not_required', content_sha256: createHash('sha256').update(text).digest('hex') } })
    emit('token', { delta: text })
    emit('turn_done', { answer_id: 'approved-1' })
    return (matches: boolean) => act(async () => { finish(matches) })
  }
  const answer = 'The first run dispatched gas at 50 EUR/MWh (historical).'

  it('speaks an approved voice answer about the past run once its check passes', async () => {
    const finish = await approvedVoiceAnswer(answer)
    expect(speechOut.speak).not.toHaveBeenCalled()
    await finish(true)
    expect(speechOut.speak).toHaveBeenCalledWith(answer)
  })

  it('drops that answer\'s pending speech when a new chat starts during its check', async () => {
    const finish = await approvedVoiceAnswer(answer)
    act(() => useChatStore.getState().startNewChat())
    await finish(true)
    expect(speechOut.speak).not.toHaveBeenCalled()
    expect(useChatStore.getState().messages.some(m => m.content === answer)).toBe(false)
  })
})
