/** LC-47: the change-set card and its one receipt in the chat transcript (Guided and Expert). */
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'
import { createChatStream, getChatHistory } from '../api/chat'
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
async function turn(mode: 'guided' | 'expert') {
  useUIStore.setState({ uiMode: mode })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><ChatPanel /></QueryClientProvider>)
  fireEvent.change(await screen.findByTestId('chat-input'), { target: { value: 'Fix the gaps.' } })
  fireEvent.click(screen.getByTestId('chat-send'))
  await waitFor(() => expect(vi.mocked(createChatStream)).toHaveBeenCalled())
  emit('session_init', { session_id: 'outcome-session', model: 'synthetic', turn_id: 'turn-1' })
}

const card = (name: string) => ({
  version: 1, kind: 'edit', tool: 'update_component', complete: true, digest: name.repeat(8), sets: [],
  says: `Set Bus ${name} eh_sk_mva`,
  rows: [{ component_class: 'Bus', name, field: 'eh_sk_mva', label: 'Short-circuit power', old: 0, new: 2500,
    unit: 'MVA', role: 'typed' }],
})
const SET = {
  version: 1, digest: 's'.repeat(64),
  items: ['tu-0', 'tu-1'].map((id, i) => ({ tool_use_id: id, tool: 'update_component', edit_card: card(`B${i + 1}`),
    source: 'proposed by the assistant', digest: id })),
  requirements: { checked: 0, says: 'The project has no confirmed requirements to check these changes against.' },
}
function raise() {
  emit('tool_pending_confirmation', { tool_use_id: 'tu-0', tool_name: 'change_set', args: { tool_use_ids: ['tu-0', 'tu-1'] },
    safety_tier: 'write', confirmation_token: 'token-1', ttl_seconds: 300, change_set: SET })
}
const receiptFrame = (decision: string, statuses: [string, string | null][]) => ({
  version: 1, decision, ticked: decision === 'approve' ? statuses.length : 0,
  landed: statuses.filter(([s]) => s === 'applied').length, failed: statuses.filter(([s]) => s === 'failed').length,
  all_applied: false,
  rows: statuses.map(([status, error_kind], i) => ({ tool_use_id: `tu-${i}`, tool: 'update_component', status,
    error_kind, label: `Set Bus B${i + 1} eh_sk_mva` })),
})

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getChatHistory).mockResolvedValue({ turns: [], last_session_id: null,
    bound_project: null, history_gap: 0, pending_turn: null })
  useUIStore.setState({ currentProject: 'Demo', activeSlidePanel: null,
    selectedComponent: null, assistantDockOpen: true, projectMismatch: null })
  useChatStore.setState({ sessionId: 'outcome-session', pending: null, messages: [], error: null,
    profileId: null, streaming: false, streamCleanup: null,
    usage: { input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_create_tokens: 0, reported: true } })
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

it.each(['guided', 'expert'] as const)('%s: one card, then one receipt with the card’s labels and the failed row’s reason', async (mode) => {
  await turn(mode)
  raise()
  expect(screen.getAllByTestId('chat-change-set-tick')).toHaveLength(2)
  // the user applies both; the card goes and each row reports
  act(() => useChatStore.getState().setPending(null))
  emit('tool_request', { tool_use_id: 'tu-0', tool_name: 'update_component', safety_tier: 'write' })
  emit('tool_result', { tool_use_id: 'tu-0', tool_name: 'update_component', result: { ok: true } })
  emit('tool_request', { tool_use_id: 'tu-1', tool_name: 'update_component', safety_tier: 'write' })
  emit('tool_error', { tool_use_id: 'tu-1', tool_name: 'update_component', error_kind: 'validation_error',
    message: 'Short-circuit power must be above 0.' })
  emit('change_set_receipt', receiptFrame('approve', [['applied', null], ['failed', 'validation_error']]))
  expect(screen.getByTestId('chat-change-set-receipt-count').textContent).toBe('1 of 2 applied · 1 failed')
  expect(screen.getAllByTestId('chat-change-set-receipt-row').map(li => li.textContent)).toEqual([
    'Bus “B1”Applied',
    'Bus “B2”Failed: Short-circuit power must be above 0',
  ])
})

it('a set the server settled takes its card with it and says one true line', async () => {
  await turn('guided')
  raise()
  emit('change_set_receipt', receiptFrame('expired', [['failed', 'confirmation_expired'], ['failed', 'confirmation_expired']]))
  expect(useChatStore.getState().pending).toBeNull()
  expect(screen.getByTestId('chat-change-set-settled').textContent).toBe('Timed out: nothing changed.')
})

it('Stop with the card open withdraws it and says nothing changed, once', async () => {
  await turn('guided')
  raise()
  fireEvent.click(screen.getByTestId('chat-abort'))
  await waitFor(() => expect(useChatStore.getState().pending).toBeNull())
  expect(screen.getByTestId('chat-change-set-settled').textContent).toBe('Stopped: nothing changed.')
  // the server's own receipt for a set the browser already settled adds no second line
  for (const decision of ['aborted', 'deny', 'expired']) {
    emit('change_set_receipt', receiptFrame(decision, [['failed', 'confirmation_denied'], ['failed', 'confirmation_denied']]))
  }
  expect(screen.getAllByTestId('chat-change-set-settled')).toHaveLength(1)
})

it('a malformed receipt frame adds no line', async () => {
  await turn('expert')
  raise()
  act(() => useChatStore.getState().setPending(null))
  emit('change_set_receipt', { version: 1, decision: 'approve', rows: [{ tool_use_id: 'tu-0', status: 'landed' }] })
  expect(screen.queryByTestId('chat-change-set-receipt')).toBeNull()
  expect(screen.queryByTestId('chat-change-set-settled')).toBeNull()
})
