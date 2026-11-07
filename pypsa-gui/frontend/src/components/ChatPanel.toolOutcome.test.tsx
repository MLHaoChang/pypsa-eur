/** Issue 04 (S1b): chat tool-line marks follow the backend's `_outcome` in Guided and Expert. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'
import { createChatStream, getChatHistory } from '../api/chat'
import ChatPanel, { guidedToolPhrase } from './ChatPanel'

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
function call(id: string, tool: string, outcome: string | null, jobId: string | null = 'fg:1', reason: string | null = null) {
  emit('tool_request', { tool_use_id: id, tool_name: tool, safety_tier: 'job' })
  emit('tool_result', { tool_use_id: id, tool_name: tool,
    result: outcome === null ? { ok: true } : { _outcome: { version: 1, outcome, job_id: jobId, reason }, status: 'completed', completed: true } })
}
const DETAILS: Record<string, string> = {
  user_restriction: 'A rule you set for this project blocks changes.',
  approval_unavailable: 'The app could not save the approval: its data folder may be full or not writable.',
  operation_pending: 'An earlier request with the same settings may still be running, so it was not started again. Ask the assistant to check it or stop it.',
  operation_conflict: 'An earlier request with other settings may still be running, so this one was not started. Ask the assistant to check it or stop it first.',
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
    ['submitted', '…', 'run_simulation (queued as of 14:02)', 'Queued: start a solve, as of 14:02'],
    ['running', '…', 'run_simulation (running as of 14:02)', 'Running: start a solve, as of 14:02'],
    ['cancel_requested', '…', 'run_simulation (stopping as of 14:02)', 'Stopping: start a solve, as of 14:02'],
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
    expect(screen.getAllByTestId('chat-tool-outcome')[0].textContent).toBe('…run_simulation (running as of 14:02)')
    vi.setSystemTime(new Date(2026, 10, 5, 14, 9))
    call('wait', 'wait_for_job', 'failed')
    expect(screen.getAllByTestId('chat-tool-outcome')[0].textContent).toBe('✕run_simulation (failed)')
    expect(screen.queryByText(/\(running/)).toBeNull()
  })

  it.each([
    ['a job result without an outcome', { status: 'completed', completed: true }],
    ['a truncated job result', { _truncated: true, length: 9000, preview: '{"status": "completed", "completed": true' }],
  ])('fails closed for %s', async (_label, result) => {
    await turn('expert')
    emit('tool_request', { tool_use_id: 'j', tool_name: 'run_simulation', safety_tier: 'job' })
    emit('tool_result', { tool_use_id: 'j', tool_name: 'run_simulation', result })
    expect(screen.getByTestId('chat-tool-outcome').textContent)
      .toBe('–run_simulation (outcome unknown: the result could not be read)')
  })

  it('LC-24: a call left open when the turn ends is settled, never Working', async () => {
    await turn('guided')
    emit('tool_request', { tool_use_id: 'w1', tool_name: 'wait_for_job', safety_tier: 'job' })
    expect(screen.getByTestId('chat-tool-label').textContent).toMatch(/^Working:/)
    emit('done', { stop_reason: 'aborted' })
    act(() => useChatStore.setState({ streaming: false, messages: [
      ...useChatStore.getState().messages,
      { id: 'u2', role: 'user', content: "What's happening?", ts: 99 },
    ] }))
    const label = screen.getByTestId('chat-tool-label').textContent
    expect(label).toMatch(/^No answer: .*\(the turn ended first\)$/)
    expect(label).not.toMatch(/Working/)
  })

  it('LC-24: Expert: an open call says no answer', async () => {
    await turn('expert')
    emit('tool_request', { tool_use_id: 'w1', tool_name: 'wait_for_job', safety_tier: 'job' })
    expect(screen.queryByTestId('chat-tool-no-answer')).toBeNull()
    emit('done', { stop_reason: 'aborted' })
    act(() => useChatStore.setState({ streaming: false, messages: [
      ...useChatStore.getState().messages,
      { id: 'u2', role: 'user', content: "What's happening?", ts: 99 },
    ] }))
    expect(screen.getByTestId('chat-tool-no-answer').textContent).toBe(' · no answer (the turn ended first)')
  })

  it('says why a queue job cannot be checked yet, in both modes', async () => {
    await turn('expert')
    call('w', 'wait_for_job', 'uncertain', 'ad:7', 'adapter_pending')
    expect(screen.getByTestId('chat-tool-outcome').textContent)
      .toBe('–wait_for_job (outcome unknown: this kind of job cannot be checked yet)')
    act(() => useUIStore.setState({ uiMode: 'guided' }))
    expect(screen.getByTestId('chat-tool-label').textContent)
      .toBe('Outcome unknown: use wait for job (this kind of job cannot be checked yet)')
  })

  it('shows a partly done write with its count in both modes', async () => {
    await turn('expert')
    emit('tool_request', { tool_use_id: 'b', tool_name: 'bulk_update_components', safety_tier: 'write' })
    emit('tool_result', { tool_use_id: 'b', tool_name: 'bulk_update_components', result: {
      _outcome: { version: 1, outcome: 'partial', reason: null, counts: { requested: 5, done: 3, not_done: 2, unknown: 0 },
        paths_not_done: ['/Generator/G4/p_nom'] }, updated: 5 } })
    expect(screen.getByTestId('chat-tool-outcome').textContent).toBe('–bulk_update_components (partly done: 3 of 5 updated)')
    act(() => useUIStore.setState({ uiMode: 'guided' }))
    expect(screen.getByTestId('chat-tool-label').textContent).toBe('Partly done: update components (3 of 5)')
  })

  it.each([
    ['bulk_update_components', 'update components'], ['batch_create_components', 'add components'],
    ['delete_component', 'delete a component'], ['batch_delete_components', 'delete components'],
    ['undo_last', 'undo the last change'],
    ['import_network_nc', 'import a NetCDF network'], ['import_csv_bundle', 'import a CSV network bundle'],
    ['import_excel', 'import an Excel network'], ['import_matpower', 'import a MATPOWER case'],
    ['import_uploaded_network', 'import an uploaded network'], ['export_network_nc', 'export the network as NetCDF'],
    ['export_csv_bundle', 'export the network as a CSV bundle'], ['export_excel', 'export the network as Excel'],
    ['export_matpower', 'export the network as a MATPOWER case'],
  ])('gives %s a plain Guided phrase', (tool, phrase) => {
    expect(guidedToolPhrase(tool)).toBe(phrase)
  })

  it.each([
    ['confirmation_denied', 'you declined it'],
    ['confirmation_expired', 'the approval expired'],
    ['confirmation_revoked', 'the approval was withdrawn'],
    ['required_guard_failed', 'the request did not pass a required check'],
    ['confirmation_args_changed', 'the request changed after you approved it'],
    ['no_progress_stop', 'stopped after repeating the same failing request'],
    ['aborted', 'the request was stopped before you answered'],
    ['user_restriction', 'a rule you set for this project blocks changes'],
    ['approval_unavailable', 'the approval could not be recorded'],
    ['round_change_cap', 'the assistant used up the changes allowed for this request'],
    ['unknown_decision', 'your answer to the approval could not be read'],
    ['project_locked', 'another change to this project was in progress'],
    ['project_lock_unavailable', "the project's edit lock could not be checked"],
    ['operation_pending', 'the same request may still be running'],
    ['operation_conflict', 'an earlier request may still be running'],
  ])('issue 04 S4a: a tool_error refused as %s reads denied with its reason in both modes', async (kind, words) => {
    await turn('expert')
    emit('tool_request', { tool_use_id: 'd', tool_name: 'bulk_update_components', safety_tier: 'write' })
    emit('tool_error', { tool_use_id: 'd', tool_name: 'bulk_update_components', error_kind: kind,
      message: "it did not run", _outcome: { version: 1, outcome: 'denied', reason: kind } })
    const row = rows().at(-1)!
    const mark = within(row).getByTestId('chat-tool-glyph')
    expect(mark.textContent).toBe('–')
    expect(mark.dataset.outcome).toBe('denied')
    expect(mark.className).toContain('text-muted')
    expect(within(row).getByTestId('chat-tool-outcome').textContent).toBe(`–bulk_update_components (denied: ${words})`)
    expect(within(row).getByTestId('chat-tool-message').textContent).toBe(DETAILS[kind] ?? 'it did not run')
    if (kind === 'user_restriction') expect(within(row).getByTestId('chat-tool-access-link')).toBeTruthy()
    else expect(within(row).queryByTestId('chat-tool-access-link')).toBeNull()
    expect(row.textContent).not.toContain(kind)
    expect(row.textContent).not.toContain('✕')
    act(() => useUIStore.setState({ uiMode: 'guided' }))
    const guidedRows = rows()
    expect(guidedRows).toHaveLength(1)
    expect(within(guidedRows[0]).getByTestId('chat-tool-label').textContent).toBe(`Denied: update components (${words})`)
    expect(within(guidedRows[0]).getByTestId('chat-tool-glyph').textContent).toBe('–')
    expect(guidedRows[0].textContent).not.toContain(kind)
  })

  it('issue 04 S4a: a declined card shows the one denied line in Guided', async () => {
    await turn('guided')
    emit('tool_request', { tool_use_id: 'd', tool_name: 'delete_component', safety_tier: 'destructive' })
    act(() => useChatStore.getState().appendMessage({ role: 'tool', content: 'denied: delete_component',
      tool_use_id: 'd', tool_name: 'delete_component' }))
    expect(screen.getByTestId('chat-tool-label').textContent).toBe('You declined. Nothing was changed.')
    emit('tool_error', { tool_use_id: 'd', tool_name: 'delete_component', error_kind: 'confirmation_denied',
      message: "deny on confirmation for 'delete_component'", _outcome: { version: 1, outcome: 'denied', reason: 'confirmation_denied' } })
    expect(screen.getAllByTestId('chat-tool-label').map(l => l.textContent)).toEqual(['Denied: delete a component (you declined it)'])
  })

  it('issue 04 S4a: Expert shows the refusal message under Details, never repr quotes or a raw kind', async () => {
    await turn('expert')
    emit('tool_error', { tool_use_id: 'g', tool_name: 'bulk_update_components', error_kind: 'required_guard_failed',
      message: "'bulk_update_components' did not run: updates: missing required 'names'.",
      _outcome: { version: 1, outcome: 'denied', reason: 'required_guard_failed' } })
    expect(screen.getByTestId('chat-tool-message').textContent)
      .toBe('bulk_update_components did not run: updates: missing required names.')
    emit('tool_error', { tool_use_id: 'h', tool_name: 'delete_component', error_kind: 'confirmation_denied',
      message: "deny on confirmation for 'delete_component'", _outcome: { version: 1, outcome: 'denied', reason: 'confirmation_denied' } })
    expect(within(rows().at(-1)!).queryByTestId('chat-tool-details')).toBeNull()
  })

  it.each(['confirmation_expired', 'approval_unavailable', 'user_restriction'])(
    'issue 04 S4a: %s opens no banner beside its denied line, and still does without one', async (kind) => {
      await turn('expert')
      emit('tool_error', { tool_use_id: 'x', tool_name: 'delete_component', error_kind: kind, message: 'it did not run',
        _outcome: { version: 1, outcome: 'denied', reason: kind } })
      expect(screen.queryByTestId('chat-error-banner')).toBeNull()
      emit('tool_error', { tool_use_id: 'y', tool_name: 'delete_component', error_kind: kind, message: 'it did not run' })
      expect(screen.getByTestId('chat-error-banner').dataset.errorKind).toBe(kind)
    })

  it.each(['expert', 'guided'] as const)('issue 04 S4a: in %s, a rule-blocked line links to Access and approvals, and only that line', async (mode) => {
    await turn(mode)
    emit('tool_error', { tool_use_id: 'r', tool_name: 'delete_component', error_kind: 'user_restriction',
      message: 'Your saved project rule blocks non-read tools.', _outcome: { version: 1, outcome: 'denied', reason: 'user_restriction' } })
    emit('tool_error', { tool_use_id: 's', tool_name: 'delete_component', error_kind: 'confirmation_expired',
      message: 'expired on confirmation for x', _outcome: { version: 1, outcome: 'denied', reason: 'confirmation_expired' } })
    const links = screen.getAllByTestId('chat-tool-access-link')
    expect(links).toHaveLength(1)
    expect(links[0].textContent).toBe('Access and approvals ›')
    expect(rows().find(r => r.contains(links[0]))!.textContent).toContain('a rule you set for this project blocks changes')
    expect(screen.queryByText(/non-read tools/)).toBeNull()
    const toggle = screen.getByRole('button', { name: 'Access and approvals' })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(links[0])
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByRole('region', { name: 'Current access and approvals' })).toBeTruthy()
    expect(document.activeElement).toBe(toggle)
  })

  it('issue 04 S4a: keeps the ✗ error line for a tool_error without an outcome', async () => {
    await turn('expert')
    emit('tool_error', { tool_use_id: 'e', tool_name: 'bulk_update_components', error_kind: 'validation_error', message: 'bus Field required' })
    expect(rows().at(-1)!.textContent).toContain('✗ bulk_update_components — validation_error: bus Field required')
    expect(within(rows().at(-1)!).queryByTestId('chat-tool-glyph')).toBeNull()
    expect(useChatStore.getState().messages.at(-1)!.tool_result).toBeUndefined()
  })

  it('issue 04 S4b: a write that raised with nothing changed reads failed, with its message', async () => {
    await turn('expert')
    emit('tool_request', { tool_use_id: 'f', tool_name: 'bulk_update_components', safety_tier: 'write' })
    emit('tool_error', { tool_use_id: 'f', tool_name: 'bulk_update_components', error_kind: 'tool_error',
      message: 'the network refused it', _outcome: { version: 1, outcome: 'failed', reason: null } })
    const row = rows().at(-1)!
    expect(within(row).getByTestId('chat-tool-outcome').textContent).toBe('✕bulk_update_components (failed)')
    expect(within(row).getByTestId('chat-tool-glyph').className).toContain('text-warn')
    expect(within(row).getByTestId('chat-tool-message').textContent).toBe('the network refused it')
    act(() => useUIStore.setState({ uiMode: 'guided' }))
    expect(within(rows().at(-1)!).getByTestId('chat-tool-label').textContent).toBe('Failed: update components')
  })

  it.each([
    ['tool_timeout', 'no answer in time'],
    ['changed_before_error', 'it stopped with an error after changing the network'],
  ])('issue 04 S4b: a write error of reason %s reads outcome unknown in both modes', async (reason, words) => {
    await turn('expert')
    emit('tool_request', { tool_use_id: 't', tool_name: 'bulk_update_components', safety_tier: 'write' })
    emit('tool_error', { tool_use_id: 't', tool_name: 'bulk_update_components', error_kind: 'tool_timeout',
      message: "tool 'bulk_update_components' exceeded the 30s execution deadline",
      _outcome: { version: 1, outcome: 'uncertain', reason } })
    expect(within(rows().at(-1)!).getByTestId('chat-tool-outcome').textContent)
      .toBe(`–bulk_update_components (outcome unknown: ${words})`)
    // UX (11): the Details text drops Python repr quotes.
    expect(within(rows().at(-1)!).getByTestId('chat-tool-message').textContent)
      .toBe('tool bulk_update_components exceeded the 30s execution deadline')
    act(() => useUIStore.setState({ uiMode: 'guided' }))
    expect(within(rows().at(-1)!).getByTestId('chat-tool-label').textContent)
      .toBe(`Outcome unknown: update components (${words})`)
  })

  it('issue 04 S4b: a write with extra edits puts the count first in both modes', async () => {
    await turn('expert')
    emit('tool_request', { tool_use_id: 'x', tool_name: 'bulk_update_components', safety_tier: 'write' })
    emit('tool_result', { tool_use_id: 'x', tool_name: 'bulk_update_components', result: {
      _outcome: { version: 1, outcome: 'partial', reason: 'extra_edits',
        counts: { requested: 1, done: 1, not_done: 0, unknown: 0, extra: 2 }, paths_not_done: [], paths_extra: ['/Bus/B1/v_nom'] } } })
    expect(screen.getByTestId('chat-tool-outcome').textContent)
      .toBe('–bulk_update_components (partly done: 2 extra changes it was not asked to make)')
    act(() => useUIStore.setState({ uiMode: 'guided' }))
    expect(screen.getByTestId('chat-tool-label').textContent)
      .toBe('Partly done: update components (2 extra changes it was not asked to make)')
  })

  it('issue 04 S4a: says another project was opened for an other_context read', async () => {
    await turn('expert')
    call('o', 'run_simulation', 'uncertain', 'fg:1', 'other_context')
    expect(screen.getByTestId('chat-tool-outcome').textContent)
      .toBe('–run_simulation (outcome unknown: another project was opened while it was being checked)')
    act(() => useUIStore.setState({ uiMode: 'guided' }))
    expect(screen.getByTestId('chat-tool-label').textContent)
      .toBe('Outcome unknown: start a solve (another project was opened while it was being checked)')
  })

  it('leaves a tool without an outcome on its plain ✓ line', async () => {
    await turn('expert')
    call('list', 'list_components', null)
    expect(rows().at(-1)!.textContent).toContain('✓ list_components')
    expect(within(rows().at(-1)!).queryByTestId('chat-tool-glyph')).toBeNull()
  })
})
