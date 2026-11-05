/** Issue 04 (S1b): chat tool-line marks follow the backend's `_outcome` in Guided and Expert. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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
  fireEvent.change(await screen.findByTestId('chat-input'), { target: { value: 'Solve the network.' } })
  fireEvent.click(screen.getByTestId('chat-send'))
  await waitFor(() => expect(vi.mocked(createChatStream)).toHaveBeenCalled())
  emit('session_init', { session_id: 'outcome-session', model: 'synthetic', turn_id: 'turn-1' })
}
function call(id: string, tool: string, outcome: string | null, jobId: string | null = 'fg:1') {
  emit('tool_request', { tool_use_id: id, tool_name: tool, safety_tier: 'job' })
  emit('tool_result', { tool_use_id: id, tool_name: tool,
    result: outcome === null ? { ok: true } : { _outcome: { version: 1, outcome, job_id: jobId, reason: null }, status: 'completed' } })
}
const rows = () => screen.getAllByTestId('chat-message').filter(r => r.dataset.role === 'tool')
const at = new Date(2026, 10, 5, 14, 2).getTime()

describe('chat tool-line outcome marks', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers({ shouldAdvanceTime: true, now: at })
    vi.mocked(getChatHistory).mockResolvedValue({ turns: [], last_session_id: null,
      bound_project: null, history_gap: 0, pending_turn: null })
    useUIStore.setState({ currentProject: 'Demo', activeSlidePanel: null,
      selectedComponent: null, assistantDockOpen: true })
    useChatStore.setState({ sessionId: 'outcome-session', pending: null, messages: [], error: null,
      profileId: null, streaming: false, streamCleanup: null,
      usage: { input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_create_tokens: 0, reported: true } })
  })
  afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks() })

  it.each([
    ['submitted', '…', 'run_simulation (queued at 14:02)', 'Queued: start a solve, as of 14:02'],
    ['running', '…', 'run_simulation (running at 14:02)', 'Running: start a solve, as of 14:02'],
    ['cancel_requested', '…', 'run_simulation (stopping at 14:02)', 'Stopping: start a solve, as of 14:02'],
    ['completed', '✓', 'run_simulation (done)', 'Done: start a solve'],
    ['failed', '✕', 'run_simulation (failed)', 'Failed: start a solve'],
    ['cancelled', '–', 'run_simulation (cancelled)', 'Cancelled: start a solve'],
    ['partial', '–', 'run_simulation (partly done)', 'Partly done: start a solve'],
    ['uncertain', '–', 'run_simulation (outcome unknown)', 'Outcome unknown: start a solve'],
  ])('%s: Expert and Guided show %s with the outcome word', async (outcome, glyph, expert, guided) => {
    await turn('expert')
    call('job-call', 'run_simulation', outcome)
    const row = rows().at(-1)!
    const mark = within(row).getByTestId('chat-tool-glyph')
    expect(mark.textContent).toBe(glyph)
    expect(mark.getAttribute('aria-hidden')).toBe('true')
    expect(mark.className).toContain(outcome === 'failed' ? 'text-warn' : glyph === '–' ? 'text-muted' : 'text-text')
    expect(within(row).getByTestId('chat-tool-outcome').textContent).toBe(`${glyph}${expert}`)
    act(() => useUIStore.setState({ uiMode: 'guided' }))
    const guidedRows = rows()
    expect(guidedRows).toHaveLength(1)
    expect(within(guidedRows[0]).getByTestId('chat-tool-label').textContent).toBe(guided)
    expect(within(guidedRows[0]).getByTestId('chat-tool-glyph').textContent).toBe(glyph)
  })

  it('never stores a completion checkmark for a job that is not done', async () => {
    await turn('expert')
    for (const outcome of ['submitted', 'running', 'cancel_requested', 'failed', 'cancelled', 'partial', 'uncertain', 'success']) {
      call(`c-${outcome}`, 'run_simulation', outcome)
    }
    const lines = useChatStore.getState().messages.filter(m => m.role === 'tool').map(m => m.content)
    expect(lines.filter(l => l.startsWith('✓'))).toEqual([])
    expect(lines.at(-1)).toBe('– run_simulation (outcome unknown)')
  })

  it('updates a running line from a later read of the same job only', async () => {
    await turn('expert')
    call('solve', 'run_simulation', 'running')
    call('other', 'wait_for_job', 'completed', 'fg:2')
    expect(screen.getAllByTestId('chat-tool-outcome')[0].textContent).toBe('…run_simulation (running at 14:02)')
    vi.setSystemTime(new Date(2026, 10, 5, 14, 9))
    call('wait', 'wait_for_job', 'failed')
    expect(screen.getAllByTestId('chat-tool-outcome')[0].textContent).toBe('✕run_simulation (failed)')
    expect(screen.queryByText(/\(running/)).toBeNull()
  })

  it('leaves a tool without an outcome on its plain ✓ line', async () => {
    await turn('expert')
    call('list', 'list_components', null)
    expect(rows().at(-1)!.textContent).toContain('✓ list_components')
    expect(within(rows().at(-1)!).queryByTestId('chat-tool-glyph')).toBeNull()
  })
})
