import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'
import { getAssistantActivity, getAssistantDecision, type ActivitySnapshot, type DecisionObservation } from '../api/assistantActivity'
import AssistantActivity from './AssistantActivity'
vi.mock('../api/assistantActivity', () => ({ getAssistantActivity: vi.fn(), getAssistantDecision: vi.fn() }))
const get = vi.mocked(getAssistantActivity)
const inspect = vi.mocked(getAssistantDecision)
function snapshot(cursor = 'epoch:1', status = 'running'): ActivitySnapshot {
  return { version: 1, project_id: 'p1', filter: 'all', cursor, revision: 1,
    resnapshot_required: false, next_page: null, total: 1, warnings: [], scheduling_available: false,
    items: [{ id: 'fg:j1', source_id: 'j1', project_id: 'p1', family: 'foreground_simulation',
      title: 'Network solve', native_status: status, status, needs_you: false, evidence_available: false }] }
}
describe('AssistantActivity', () => {
  beforeEach(() => {
    get.mockReset()
    inspect.mockReset()
    useUIStore.setState({ currentProject: 'Project A' })
    useChatStore.setState({ sessionId: 'session-a', sourceGapDraft: null, newChatSeq: 0 })
  })
  afterEach(() => vi.useRealTimers())
  it('observes unavailable native states without sending work', async () => {
    get.mockResolvedValue(snapshot('epoch:1', 'unavailable'))
    render(<AssistantActivity onConversation={vi.fn()} />)
    expect(await screen.findByText('Network solve')).toBeTruthy()
    expect(screen.getByText(/cannot be established/)).toBeTruthy()
    expect(get.mock.calls[0].slice(0, 2)).toEqual(['Project A', 'all'])
    expect(screen.queryByRole('button', { name: /cancel|approve|retry job/i })).toBeNull()
  })
  it('never shows late project data after a switch', async () => {
    let finish!: (value: ActivitySnapshot) => void
    get.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    get.mockResolvedValue(snapshot('b:1', 'failed'))
    render(<AssistantActivity onConversation={vi.fn()} />)
    act(() => useUIStore.setState({ currentProject: 'Project B' }))
    expect(await screen.findByText('failed')).toBeTruthy()
    await act(async () => finish(snapshot('old:1', 'completed')))
    expect(screen.queryByText('completed')).toBeNull()
  })
  it('marks disconnected states stale and recovers a fresh snapshot', async () => {
    get.mockResolvedValueOnce(snapshot()).mockRejectedValueOnce(new Error('offline'))
    const recovered = snapshot('new:1', 'failed')
    recovered.resnapshot_required = true
    get.mockResolvedValue(recovered)
    render(<AssistantActivity onConversation={vi.fn()} />)
    await screen.findByText('Network solve')
    fireEvent(window, new Event('online'))
    expect(await screen.findByRole('alert')).toBeTruthy()
    fireEvent(window, new Event('online'))
    expect(await screen.findByText('Activity recovered from a fresh snapshot.')).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
  })
  it('opens the exact current confirmation without approving it', async () => {
    const data = snapshot()
    data.items = [{ ...data.items[0], family: 'confirmation', status: 'waiting_for_approval',
      native_status: 'waiting_for_approval', session_id: 'session-a', needs_you: true }]
    get.mockResolvedValue(data)
    const open = vi.fn()
    render(<AssistantActivity onConversation={open} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Open originating chat' }))
    expect(open).toHaveBeenCalledOnce()
    expect(get).toHaveBeenCalledOnce()
  })
  it('reports scheduling unavailable and aborts observation on close', async () => {
    get.mockResolvedValue(snapshot())
    const { unmount } = render(<AssistantActivity onConversation={vi.fn()} />)
    await screen.findByText('Network solve')
    fireEvent.click(screen.getByRole('button', { name: 'Scheduled' }))
    expect(await screen.findByText('Scheduled work is not available yet.')).toBeTruthy()
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2))
    const signal = get.mock.calls[1][2]?.signal
    unmount()
    expect(signal?.aborted).toBe(true)
  })
  it('distinguishes retained observations from live work and grants no recovered control', async () => {
    const data = snapshot('recovered:1', 'needs_review')
    data.items[0] = { ...data.items[0], native_status: 'unknown', last_observed_status: 'running', needs_you: true,
      observation: 'retained', observed_at: 1792000000,
      reason: 'Last observed work has no matching live worker. It was not restarted.' }
    get.mockResolvedValue(data)
    const open = vi.fn()
    const { container } = render(<AssistantActivity onConversation={open} />)
    expect(await screen.findByText(/Retained observation/)).toBeTruthy()
    expect(screen.getByText(/It was not restarted/)).toBeTruthy()
    expect(screen.getByText('Native status: unknown')).toBeTruthy()
    expect(screen.getByText('Last recorded native status: running')).toBeTruthy()
    expect(container.querySelector('time')?.dateTime).toBe(new Date(1792000000000).toISOString())
    expect(screen.getByText(/Unobserved outcomes remain unknown/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /approve|retry|resume|cancel|open originating/i })).toBeNull()
    expect(open).not.toHaveBeenCalled()
  })
  it('shows missing retained history as unavailable rather than proof nothing ran', async () => {
    const data = snapshot()
    data.items = []
    data.warnings = ['activity_history_unavailable']
    get.mockResolvedValue(data)
    render(<AssistantActivity onConversation={vi.fn()} />)
    expect(await screen.findByText(/missing history does not mean work never ran/)).toBeTruthy()
  })
  it('does not crash or render an invalid observation date', async () => {
    const data = snapshot()
    data.items[0] = { ...data.items[0], observation: 'retained', observed_at: 1e100 }
    get.mockResolvedValue(data)
    const { container } = render(<AssistantActivity onConversation={vi.fn()} />)
    expect(await screen.findByText('Retained observation')).toBeTruthy()
    expect(container.querySelector('time')).toBeNull()
  })
  it('explains incomplete background observation without offering replay', async () => {
    const data = snapshot()
    data.items = []
    data.warnings = ['activity_completion_observation_incomplete']
    get.mockResolvedValue(data)
    render(<AssistantActivity onConversation={vi.fn()} />)
    expect(await screen.findByText(/Some registered jobs could not be observed through completion/)).toBeTruthy()
    expect(screen.getByText(/missing history does not establish whether work finished/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /retry|resume|cancel|approve/i })).toBeNull()
  })
  function question() {
    const data = snapshot()
    data.items[0] = { ...data.items[0], family: 'question', status: 'waiting_for_input',
      native_status: 'waiting_for_input', decision_id: 'decision1', decision_revision: 1,
      decision_digest: 'digest1', session_id: 'old-session', needs_you: true }
    return data
  }
  function detail(): DecisionObservation {
    return { source_id: 'decision1', project_id: 'p1', session_id: 'old-session', tool_use_id: 'original-call',
      tool_name: 'ask_user', native_status: 'waiting_for_input', revision: 1, decision_digest: 'digest1',
      inspection_only: true, evidence_available: false, reason: 'Read the original question.',
      question: { title: 'Basis', question: 'Which basis?', detail: '<script>untrusted text</script>',
        allow_free_text: true, multi_select: false, intent: 'choice', options: [{ label: 'Battery', description: '', recommended: true }] } }
  }
  it('inspects an old question as text without restoring controls or rebinding the chat', async () => {
    get.mockResolvedValue(question())
    inspect.mockResolvedValue(detail())
    const open = vi.fn()
    const { container } = render(<AssistantActivity onConversation={open} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Inspect decision' }))
    expect(await screen.findByText('Which basis?')).toBeTruthy()
    expect(screen.getByText('Battery (recommended)')).toBeTruthy()
    expect(screen.getByText('<script>untrusted text</script>')).toBeTruthy()
    expect(container.querySelector('script')).toBeNull()
    expect(screen.getByText(/original-call/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /approve|answer|retry|resume|open originating/i })).toBeNull()
    expect(useChatStore.getState().sessionId).toBe('session-a')
    expect(open).not.toHaveBeenCalled()
    expect(inspect.mock.calls[0][0]).toEqual(question().items[0])
  })
  it('refuses stale inspection and offers a visible refresh route', async () => {
    get.mockResolvedValue(question())
    inspect.mockRejectedValue(new Error('409 stale'))
    render(<AssistantActivity onConversation={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Inspect decision' }))
    expect(await screen.findByRole('alert')).toHaveProperty('textContent', expect.stringContaining('Refresh Activity'))
    expect(screen.queryByText('Which basis?')).toBeNull()
  })
  it('prepares an exact source-gap request in the current chat without sending or restoring history', async () => {
    const data = question()
    data.items[0].family = 'source_gap'
    get.mockResolvedValue(data)
    const prepared = { message: 'Recheck the exact request shown in the checked follow-up card.',
      checked_scope: { version: 1, project_id: 'p1', profile: 'generator-dispatch/1', entity_id: 'G1' },
      source_gap_ref: { source_id: 'a'.repeat(64), revision: 1, digest: 'b'.repeat(64) } }
    inspect.mockResolvedValue({ ...detail(), question: null, prepared_followup: prepared,
      source_gap: { answer_id: 'old-answer', turn_id: 'old-turn',
        request_scope: { project_id: 'p1', profile: 'generator-dispatch/1', entity_id: 'G1' },
        slots: [{ metric: 'dispatch_energy', disposition: 'unavailable', reason: 'unverified_solve' }] },
      followups: [{ answer_id: 'later-answer', turn_id: 'later-turn', session_id: 'other-chat',
        request_complete: true, observed_at: 1792000000 }] })
    const open = vi.fn()
    render(<AssistantActivity onConversation={open} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Inspect source gap' }))
    expect(await screen.findByText(/All requested metrics were answered at publication/)).toBeTruthy()
    expect(screen.getByText(/They do not resolve this historical gap/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Prepare checked follow-up' }))
    expect(useChatStore.getState().sourceGapDraft).toEqual({ ...prepared, projectName: 'Project A', chatSequence: 0 })
    expect(open).toHaveBeenCalledOnce()
    expect(useChatStore.getState().sessionId).toBe('session-a')
    expect(useChatStore.getState().requestQueue).toEqual([])
  })
  it('inspects historical missing slots and exact request scope without executing work', async () => {
    const data = question()
    data.items[0] = { ...data.items[0], family: 'source_gap', title: 'Checked answer source gap',
      status: 'needs_review', native_status: 'source_gap_recorded' }
    get.mockResolvedValue(data)
    inspect.mockResolvedValue({ ...detail(), question: null, tool_name: 'checked_answer',
      native_status: 'source_gap_recorded', reason: 'A later turn does not resolve this historical gap.',
      source_gap: { answer_id: 'answer-original', turn_id: 'turn-original',
        request_scope: { profile: 'generator-cost/1', project_id: 'requested-project',
          entity_id: '<img src=x onerror=alert(1)>', start: '2026-01-01', end: '2026-01-02',
          cost_basis: { currency: 'EUR', price_year: 2026, annual_fixed_cost_fraction: 1 },
          evidence_id: 'retained-original', comparison_evidence_id: 'baseline-original' },
        slots: [{ metric: 'total_cost', disposition: 'unavailable', reason: 'invalid_or_missing_raw_data' }] } })
    const { container } = render(<AssistantActivity onConversation={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Inspect source gap' }))
    expect(await screen.findByText(/Answer: answer-original/)).toBeTruthy()
    expect(screen.getByText(/2026-01-01 to 2026-01-02/)).toBeTruthy()
    expect(screen.getByText(/Entity: <img src=x onerror=alert\(1\)>/)).toBeTruthy()
    expect(screen.getByText(/Requested baseline evidence: baseline-original/)).toBeTruthy()
    expect(screen.getByText(/total_cost: unavailable \(invalid_or_missing_raw_data\)/)).toBeTruthy()
    expect(screen.getByText(/A later turn does not resolve/)).toBeTruthy()
    expect(container.querySelector('img')).toBeNull()
    expect(screen.queryByRole('button', { name: /approve|answer|retry|resume|open originating/i })).toBeNull()
    expect(useChatStore.getState().sessionId).toBe('session-a')
  })
  it('aborts inspection on project changes and ignores late private data', async () => {
    get.mockResolvedValue(question())
    let finish!: (value: DecisionObservation) => void
    inspect.mockImplementation(() => new Promise(resolve => { finish = resolve }))
    render(<AssistantActivity onConversation={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Inspect decision' }))
    await waitFor(() => expect(inspect).toHaveBeenCalledOnce())
    const signal = inspect.mock.calls[0][1]
    act(() => useUIStore.setState({ currentProject: 'Project B' }))
    expect(signal.aborted).toBe(true)
    await act(async () => finish(detail()))
    expect(screen.queryByText('Which basis?')).toBeNull()
  })
})
