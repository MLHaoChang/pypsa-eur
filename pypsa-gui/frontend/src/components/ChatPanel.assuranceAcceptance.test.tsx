/** Implemented public UI/voice publication boundary, always run in CI. */
import { createHash, webcrypto } from 'node:crypto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'
import { createChatStream, getChatHistory } from '../api/chat'
import * as speechOut from '../utils/speechOut'
import ChatPanel from './ChatPanel'

const unsafe = 'Ghost-9 is safe at 75 MW and the simulation completed.'
const speech: { onFinal?: (text: string) => void } = {}
vi.mock('../api/reliability', () => ({
  CHECKED_ANSWERS_QUERY_KEY: ['chat', 'checked-answer-capabilities'],
  getCheckedAnswerCapabilities: vi.fn().mockResolvedValue({ version: 1, mode: 'strict',
    profiles: { 'storage-unit-dispatch/1': { enabled: true } } }),
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
async function start(voice = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><ChatPanel /></QueryClientProvider>)
  const input = await screen.findByTestId('chat-input')
  if (voice) act(() => speech.onFinal?.('Read the selected results.'))
  else fireEvent.change(input, { target: { value: 'Read the selected results.' } })
  fireEvent.click(screen.getByTestId('chat-send'))
  await waitFor(() => expect(vi.mocked(createChatStream)).toHaveBeenCalled())
  emit('session_init', { session_id: 'assurance-session', model: 'synthetic', turn_id: 'turn-1',
    assurance: { version: 1, mode: 'strict', state: 'checking' } })
}
function approve(text: string) {
  emit('answer_approved', { version: 1, answer_id: 'approved-1', turn_id: 'turn-1', verdict: 'limited',
    receipt: { policy_version: '1', evidence_ids: [], checked_block_ids: [],
      limitations: ['No completed simulation is available.'], reviewer_status: 'not_required',
      content_sha256: createHash('sha256').update(text).digest('hex') } })
}

describe('implemented assurance publication boundary', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getChatHistory).mockResolvedValue({ turns: [], last_session_id: null,
      bound_project: null, history_gap: 0, pending_turn: null })
    vi.stubGlobal('speechSynthesis', { speak: vi.fn(), cancel: vi.fn(), speaking: false })
    vi.stubGlobal('SpeechSynthesisUtterance', class { constructor(public text: string) {} })
    vi.stubGlobal('crypto', webcrypto)
    vi.spyOn(speechOut, 'speak').mockImplementation(() => {})
    vi.spyOn(speechOut, 'cancelSpeech').mockImplementation(() => {})
    useUIStore.setState({ currentProject: 'Demo', activeSlidePanel: null,
      selectedComponent: null, assistantDockOpen: true, assistantSpeakEnabled: true })
    useChatStore.setState({ sessionId: 'assurance-session', pending: null, messages: [], error: null,
      profileId: null, streaming: false, streamCleanup: null,
      usage: { input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_create_tokens: 0, reported: true } })
  })
  afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

  it('shows a software clarification and sends its choice as the next checked user request', async () => {
    await start()
    const text = 'Which cost quantity do you mean?'
    emit('answer_approved', { version: 1, answer_id: 'approved-1', turn_id: 'turn-1', verdict: 'limited',
      receipt: { policy_version: '1', content_sha256: createHash('sha256').update(text).digest('hex'),
        evidence_ids: [], checked_block_ids: [], limitations: ['cost_quantity_required'],
        reviewer_status: 'not_required', request_complete: false,
        request_scope: { profile: 'generator-cost/1', entity_id: 'G1' },
        result_slots: [{ metric: 'total_cost', disposition: 'needs_clarification', reason: 'cost_quantity_required' }] } })
    emit('token', { delta: text })
    emit('choice_request', { tool_use_id: 'turn-1', title: 'Cost quantity', question: text,
      options: [{ label: 'Marginal dispatch expenditure' }, { label: 'Total declared-basis cost' }], allow_free_text: true })
    emit('turn_done', { answer_id: 'approved-1' })
    expect(await screen.findByText('Clarification needed')).toBeTruthy()
    expect(screen.queryByText('Requested results checked')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Total declared-basis cost/ }))
    await waitFor(() => expect(vi.mocked(createChatStream)).toHaveBeenCalledTimes(2))
    expect(vi.mocked(createChatStream).mock.calls[1][0].message).toBe('Total declared-basis cost')
    expect(vi.mocked(createChatStream).mock.calls[1][0].assurance).toEqual({ version: 1, mode: 'strict' })
  })

  it.each(['token', 'thinking'])('P2: withholds unapproved %s frames from visible text', async event => {
    await start()
    emit(event, { delta: unsafe })
    expect(screen.queryByText(unsafe)).toBeNull()
    expect(useChatStore.getState().messages.some(m => m.content.includes(unsafe) || m.thinking?.includes(unsafe))).toBe(false)
  })

  it.each([
    ['assurance_publication_failed', 'The checked answer could not be published'],
    ['assurance_access_revoked', 'You no longer have access to this project'],
  ])('a commit refusal %s ends the turn with its banner and no retry', async (kind, title) => {
    await start()
    emit('token', { delta: unsafe })
    emit('error', { error_kind: kind, message: 'No answer was published.' })
    emit('session_done', { reason: kind })
    expect(useChatStore.getState().error?.error_kind).toBe(kind)
    expect(useChatStore.getState().streaming).toBe(false)
    expect(within(screen.getByTestId('chat-error-banner')).getByText(title)).toBeTruthy()
    expect(screen.queryByTestId('chat-error-retry')).toBeNull()
    expect(screen.queryByText(unsafe)).toBeNull()
  })

  it('P2/P6: turn_done cannot speak text without an approval receipt', async () => {
    await start(true)
    emit('token', { delta: unsafe })
    emit('turn_done', {})
    expect(speechOut.speak).not.toHaveBeenCalled()
  })

  it('P2/P6: an approved limited answer remains usable through ordinary voice', async () => {
    await start(true)
    const text = 'No completed simulation is available.'
    approve(text)
    emit('token', { delta: text })
    emit('turn_done', { answer_id: 'approved-1' })
    expect(await screen.findByText(text)).toBeTruthy()
    await waitFor(() => expect(speechOut.speak).toHaveBeenCalledWith(text))
  })

  it.each([true, false])('publishes checked storage text and voice with completeness %s', async complete => {
    await start(true)
    const text = complete ? 'StorageUnit Battery: charging energy 10 MWh.'
      : 'StorageUnit Battery: charging energy 10 MWh. Storage cost is unavailable.'
    emit('answer_approved', { version: 1, answer_id: 'approved-storage', turn_id: 'turn-1',
      verdict: complete ? 'passed' : 'limited', receipt: {
        policy_version: '1', content_sha256: createHash('sha256').update(text).digest('hex'),
        evidence_ids: ['storage-evidence'], checked_block_ids: ['charged_energy'],
        limitations: ['Storage durations are used; engineering certification is not established.'],
        reviewer_status: 'not_required', request_complete: complete,
        request_scope: { profile: 'storage-unit-dispatch/1', entity_id: 'Battery', retain: false },
        result_slots: [{ metric: 'charged_energy', disposition: 'answered', requested_snapshots: 3, valid_snapshots: 3 },
          ...(!complete ? [{ metric: 'total_cost', disposition: 'unavailable', reason: 'unsupported_metric' }] : [])],
      } })
    emit('token', { delta: text })
    emit('turn_done', { answer_id: 'approved-storage' })
    expect(await screen.findByText(text)).toBeTruthy()
    await waitFor(() => expect(speechOut.speak).toHaveBeenCalledWith(text))
    expect(screen.getByText(/storage unit dispatch/)).toBeTruthy()
    if (complete) expect(screen.getByText('Requested results checked')).toBeTruthy()
    else {
      expect(screen.queryByText('Requested results checked')).toBeNull()
      expect(screen.getByText(/total cost: unavailable/)).toBeTruthy()
    }
  })

  it.each(['submitted', 'running', 'partial', 'failed', 'uncertain', 'cancel_requested'])('P3: %s tool outcomes never get a completion checkmark', async status => {
    await start()
    emit('tool_result', { tool_use_id: 'job-call', tool_name: 'run_simulation',
      result: { status, _assurance: { version: 1, outcome: status, evidence_ids: [] } } })
    expect(useChatStore.getState().messages.some(m => m.role === 'tool' && m.content.startsWith('✓'))).toBe(false)
  })

  it.each([true, false])('publishes checked storage economics and voice with completeness %s', async complete => {
    await start(true)
    const text = 'StorageUnit Battery: total direct cost 51 EUR. Charging electricity purchases are excluded.'
      + (complete ? '' : ' NPV is unavailable.')
    emit('answer_approved', { version: 1, answer_id: 'approved-storage-cost', turn_id: 'turn-1',
      verdict: complete ? 'passed' : 'limited', receipt: {
        policy_version: '1', content_sha256: createHash('sha256').update(text).digest('hex'),
        evidence_ids: ['storage-cost-evidence'], checked_block_ids: ['total_cost'],
        limitations: ['Declared EUR price year 2026; annual fixed-cost fraction 0.5.'],
        reviewer_status: 'not_required', request_complete: complete,
        request_scope: { profile: 'storage-unit-cost/1', entity_id: 'Battery', retain: false,
          cost_basis: { currency: 'EUR', price_year: 2026, annual_fixed_cost_fraction: 0.5 } },
        result_slots: [{ metric: 'total_cost', disposition: 'answered', requested_snapshots: 3, valid_snapshots: 3 },
          ...(!complete ? [{ metric: 'NPV', disposition: 'unavailable', reason: 'unsupported_metric' }] : [])],
      } })
    emit('token', { delta: text })
    emit('turn_done', { answer_id: 'approved-storage-cost' })
    expect(await screen.findByText(text)).toBeTruthy()
    await waitFor(() => expect(speechOut.speak).toHaveBeenCalledWith(text))
    expect(screen.getByText(/storage unit cost/)).toBeTruthy()
    expect(screen.getByText(/Declared EUR price year 2026/)).toBeTruthy()
    if (complete) expect(screen.getByText('Requested results checked')).toBeTruthy()
    else {
      expect(screen.queryByText('Requested results checked')).toBeNull()
      expect(screen.getByText(/NPV: unavailable/)).toBeTruthy()
    }
  })

  it('P4/P5: unapproved artifact links are not downloadable from a tool card', async () => {
    await start()
    emit('tool_result', { tool_use_id: 'chart-call', tool_name: 'create_chart', result: {
      filename: 'chart.png', download_url: '/api/projects/project-id/uploads/0123456789abcdef/blob',
      _assurance: { version: 1, outcome: 'submitted', evidence_ids: [] },
    } })
    expect(screen.queryByTestId('assistant-file-download')).toBeNull()
  })

  it('P5: checking progress is visible without exposing planner reasoning', async () => {
    await start()
    emit('answer_progress', { version: 1, turn_id: 'turn-1', stage: 'checking_claims', status: 'running' })
    expect(screen.getByText(/checking.*(claims|evidence|answer)/i)).toBeTruthy()
    expect(screen.queryByTestId('chat-thinking-block')).toBeNull()
  })

  it('does not publish an approval the receipt parser refuses', async () => {
    await start(true)
    const text = 'No completed simulation is available.'
    emit('answer_approved', {
      version: 2,
      answer_id: 'approved-1',
      turn_id: 'turn-1',
      verdict: 'limited',
      receipt: {
        policy_version: '1',
        evidence_ids: [],
        checked_block_ids: [],
        limitations: [],
        reviewer_status: 'not_required',
        content_sha256: createHash('sha256').update(text).digest('hex'),
      },
    })
    emit('token', { delta: text })
    emit('turn_done', { answer_id: 'approved-1' })
    expect(screen.queryByText(text)).toBeNull()
    expect(speechOut.speak).not.toHaveBeenCalled()
    expect(useChatStore.getState().messages.some(m => m.role === 'assistant' && m.content === text)).toBe(false)
  })

  it('withholds an approved stream whose bytes do not match its receipt', async () => {
    await start(true)
    approve('A checked limited answer.')
    emit('token', { delta: unsafe })
    emit('turn_done', { answer_id: 'approved-1' })
    await waitFor(() => expect(useChatStore.getState().error?.error_kind).toBe('assurance_publication_failed'))
    expect(screen.queryByText(unsafe)).toBeNull()
    expect(speechOut.speak).not.toHaveBeenCalled()
  })

  it('rejects an approval from a different turn generation', async () => {
    await start(true)
    emit('session_init', { session_id: 'assurance-session', turn_id: 'current-turn',
      assurance: { version: 1, mode: 'strict', state: 'checking' } })
    approve(unsafe)
    emit('token', { delta: unsafe })
    emit('turn_done', { answer_id: 'approved-1' })
    expect(screen.queryByText(unsafe)).toBeNull()
    expect(speechOut.speak).not.toHaveBeenCalled()
  })

  it('discards callbacks from a stream after the user aborts it', async () => {
    await start(true)
    fireEvent.click(screen.getByTestId('chat-abort'))
    emit('session_init', { session_id: 'stale-session' })
    emit('token', { delta: unsafe })
    emit('turn_done', {})
    expect(screen.queryByText(unsafe)).toBeNull()
    expect(speechOut.speak).not.toHaveBeenCalled()
  })

  it('discards callbacks after the user switches project', async () => {
    await start(true)
    act(() => useUIStore.setState({ currentProject: 'Another project' }))
    emit('session_init', { session_id: 'stale-session' })
    emit('token', { delta: unsafe })
    emit('turn_done', {})
    expect(screen.queryByText(unsafe)).toBeNull()
    expect(speechOut.speak).not.toHaveBeenCalled()
  })

  it('requires a server-issued turn identity before accepting approval', async () => {
    await start(true)
    emit('session_init', { session_id: 'assurance-session',
      assurance: { version: 1, mode: 'strict', state: 'checking' } })
    approve(unsafe)
    emit('token', { delta: unsafe })
    emit('turn_done', { answer_id: 'approved-1' })
    expect(screen.queryByText(unsafe)).toBeNull()
    expect(speechOut.speak).not.toHaveBeenCalled()
  })
})
