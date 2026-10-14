import { createHash, webcrypto } from 'node:crypto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { client } from '../api/client'
import { useChatStore, type ChatMessage } from '../store/chatStore'
import { useUIStore } from '../store/uiStore'
import CompanionOutcome from './CompanionOutcome'

vi.mock('../api/client', () => ({ client: { get: vi.fn() } }))
const text = 'Generator G1: energy 10 MWh; marginal dispatch cost 100 EUR.'
const receipt = {
  policy_version: '1', content_sha256: createHash('sha256').update(text).digest('hex'),
  evidence_ids: ['evidence-1'], checked_block_ids: ['energy', 'cost'], limitations: ['No model certification.'],
  reviewer_status: 'not_required', request_complete: true,
  request_scope: { profile: 'generator-cost/1', project_id: 'project-1', entity_id: 'G1',
    start: '2026-01-01T00:00:00', end: '2026-01-01T01:00:00' },
  result_slots: [{ metric: 'cost', disposition: 'answered' as const }],
}
function record(over: Record<string, unknown> = {}) {
  return { version: 1, answer_id: 'answer-1', turn_id: 'turn-1', project_id: 'project-1',
    text, verdict: 'passed', receipt, ...over }
}
function checkedMessages(): ChatMessage[] {
  return [
    { id: 'u1', role: 'user', content: 'Show the cost', requiresChecked: true, origin_project: 'Demo', ts: 1 },
    { id: 'a1', role: 'assistant', content: text, answer_id: 'answer-1', answer_turn_id: 'turn-1', origin_project: 'Demo', receipt, ts: 2 },
  ]
}
beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('crypto', webcrypto)
  useUIStore.setState({ currentProject: 'Demo', assistantDockOpen: false, projectMismatch: null })
  useChatStore.setState({ messages: [], streaming: false, error: null, sessionId: 'session-1',
    newChatSeq: 0, utteranceGeneration: 1, profileId: null, boundProfileId: null })
  vi.mocked(client.get).mockImplementation(async url => ({ data:
    String(url).endsWith('/reliability/context') ? { version: 1, project_id: 'project-1' } : record(),
  }))
})
afterEach(() => vi.unstubAllGlobals())

describe('closed companion publication', () => {
  it('shows the canonical ordinary answer, labels it unchecked and opens the transcript without sending work', () => {
    useChatStore.setState({ messages: [{ id: 'a', role: 'assistant', content: 'An ordinary response.', ts: 1 }] })
    render(<CompanionOutcome />)
    expect(screen.getByText('An ordinary response.')).toBeTruthy()
    expect(screen.getByText('Answer not checked')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Open transcript' }))
    expect(useUIStore.getState().assistantDockOpen).toBe(true)
    expect(client.get).not.toHaveBeenCalled()
  })

  it('revalidates the owned checked record and exact bytes before showing scope or evidence actions', async () => {
    useChatStore.setState({ messages: checkedMessages() })
    render(<CompanionOutcome />)
    expect(screen.queryByText(text)).toBeNull()
    expect(await screen.findByText(text)).toBeTruthy()
    expect(screen.getAllByText('Requested results checked').length).toBeGreaterThan(0)
    expect(screen.getByRole('link', { name: 'View answer record' }).getAttribute('href')).toBe('/api/chat/answers/answer-1')
    expect(screen.getByText(/project-1/)).toBeTruthy()
    expect(screen.getByText(/generator-cost\/1/)).toBeTruthy()
    expect(screen.getByTestId('companion-outcome').getAttribute('data-answer-id')).toBe('answer-1')
  })

  it.each([
    ['wrong answer', { answer_id: 'other-answer' }],
    ['wrong turn', { turn_id: 'other-turn' }],
    ['wrong active project', { project_id: 'other-project' }],
    ['modified text', { text: 'Cost 999 EUR.' }],
    ['copied receipt', { receipt: { ...receipt, checked_block_ids: ['unrelated'] } }],
    ['incoherent completion', { receipt: { ...receipt, result_slots: [{ metric: 'cost', disposition: 'unavailable' }] } }],
    ['unsupported scope', { receipt: { ...receipt, request_scope: { profile: 'unsupported-domain/1' } } }],
  ])('withholds %s instead of downgrading or publishing an unrelated checked label', async (_, over) => {
    vi.mocked(client.get).mockImplementation(async url => ({ data:
      String(url).endsWith('/reliability/context') ? { version: 1, project_id: 'project-1' } : record(over),
    }))
    useChatStore.setState({ messages: checkedMessages() })
    render(<CompanionOutcome />)
    expect(await screen.findByText('Checked answer unavailable')).toBeTruthy()
    expect(screen.queryByText(text)).toBeNull()
    expect(screen.queryByText('Answer not checked')).toBeNull()
    expect(screen.queryByRole('link', { name: 'View answer record' })).toBeNull()
  })

  it('withholds missing, pruned, revoked or corrupt server evidence', async () => {
    vi.mocked(client.get).mockRejectedValue(new Error('answer_evidence_unavailable'))
    useChatStore.setState({ messages: checkedMessages() })
    render(<CompanionOutcome />)
    expect(await screen.findByText('Checked answer unavailable')).toBeTruthy()
    expect(screen.queryByText(text)).toBeNull()
  })

  it('suppresses raw downloads and prose when the latest request requires a missing approval', () => {
    useChatStore.setState({ messages: [
      { id: 'u1', role: 'user', content: 'Check results', requiresChecked: true, ts: 1 },
      { id: 'a1', role: 'assistant', content: 'Invented 999 EUR.', ts: 2 },
      { id: 't1', role: 'tool', content: '', ts: 3,
        tool_result: { download_url: '/api/projects/Demo/uploads/0123456789abcdef/blob' } },
    ] })
    render(<CompanionOutcome />)
    expect(screen.getByText('Checked answer unavailable')).toBeTruthy()
    expect(screen.queryByText('Invented 999 EUR.')).toBeNull()
    expect(screen.queryByTestId('companion-download')).toBeNull()
  })

  it('ignores an old lookup when a new request replaces the answer', async () => {
    let resolveAnswer!: (value: unknown) => void
    vi.mocked(client.get).mockImplementation(url => String(url).endsWith('/reliability/context')
      ? Promise.resolve({ data: { version: 1, project_id: 'project-1' } })
      : new Promise(resolve => { resolveAnswer = resolve }))
    useChatStore.setState({ messages: checkedMessages() })
    render(<CompanionOutcome />)
    await waitFor(() => expect(client.get).toHaveBeenCalledTimes(2))
    act(() => useChatStore.setState({ messages: [{ id: 'u2', role: 'user', content: 'Next request', ts: 3 }] }))
    await act(async () => resolveAnswer({ data: record() }))
    expect(screen.queryByText(text)).toBeNull()
    expect(screen.queryByTestId('companion-outcome')).toBeNull()
  })

  it('drops a checked card immediately on project/generation change and ignores late access checks', async () => {
    let resolveAnswer!: (value: unknown) => void
    vi.mocked(client.get).mockImplementation(url => String(url).endsWith('/reliability/context')
      ? Promise.resolve({ data: { version: 1, project_id: 'project-1' } })
      : new Promise(resolve => { resolveAnswer = resolve }))
    useChatStore.setState({ messages: checkedMessages() })
    render(<CompanionOutcome />)
    await waitFor(() => expect(client.get).toHaveBeenCalledTimes(2))
    act(() => { useUIStore.setState({ currentProject: 'Other' }); useChatStore.setState({ utteranceGeneration: 2 }) })
    await act(async () => resolveAnswer({ data: record() }))
    expect(screen.queryByText(text)).toBeNull()
    expect(screen.queryByTestId('companion-outcome')).toBeNull()
  })

  it('keeps the full canonical text available across expansion without a model rewrite or timeout', () => {
    vi.useFakeTimers()
    try {
      const long = 'One exact numerical sentence: 100 EUR. '.repeat(25)
      useChatStore.setState({ messages: [{ id: 'a1', role: 'assistant', content: long, ts: 1 }] })
      render(<CompanionOutcome />)
      const body = screen.getByTestId('companion-answer-text')
      expect(body.textContent?.trim()).toBe(long.trim())
      fireEvent.click(screen.getByRole('button', { name: 'Expand answer' }))
      expect(body.textContent?.trim()).toBe(long.trim())
      act(() => vi.advanceTimersByTime(60_000))
      expect(screen.getByTestId('companion-answer-text').textContent?.trim()).toBe(long.trim())
      expect(client.get).not.toHaveBeenCalled()
    } finally { vi.useRealTimers() }
  })

  it('rejects a matching unsupported profile even when the client and owned record agree', async () => {
    const unsupported = { ...receipt, request_scope: { ...receipt.request_scope, profile: 'unknown-domain/1' } }
    vi.mocked(client.get).mockImplementation(async url => ({ data: String(url).endsWith('/reliability/context')
      ? { version: 1, project_id: 'project-1' } : record({ receipt: unsupported }) }))
    useChatStore.setState({ messages: checkedMessages().map(m => m.receipt ? { ...m, receipt: unsupported } : m) })
    render(<CompanionOutcome />)
    expect(await screen.findByText('Checked answer unavailable')).toBeTruthy()
    expect(screen.queryByText(text)).toBeNull()
  })

  it('rejects corrupted bytes even when both receipts carry the same malformed content claim', async () => {
    const corrupt = { ...receipt, content_sha256: 'a'.repeat(64) }
    vi.mocked(client.get).mockImplementation(async url => ({ data: String(url).endsWith('/reliability/context')
      ? { version: 1, project_id: 'project-1' } : record({ receipt: corrupt }) }))
    useChatStore.setState({ messages: checkedMessages().map(m => m.receipt ? { ...m, receipt: corrupt } : m) })
    render(<CompanionOutcome />)
    expect(await screen.findByText('Checked answer unavailable')).toBeTruthy()
    expect(screen.queryByText(text)).toBeNull()
  })

  it('shows a software clarification without claiming checked results or offering a value export', async () => {
    const question = 'Which cost quantity do you mean?'
    const clarification = { ...receipt, content_sha256: createHash('sha256').update(question).digest('hex'),
      evidence_ids: [], checked_block_ids: [], request_complete: false,
      result_slots: [{ metric: 'cost', disposition: 'needs_clarification' as const }] }
    vi.mocked(client.get).mockImplementation(async url => ({ data: String(url).endsWith('/reliability/context')
      ? { version: 1, project_id: 'project-1' } : record({ text: question, verdict: 'limited', receipt: clarification }) }))
    useChatStore.setState({ messages: checkedMessages().map(m => m.receipt ? { ...m, content: question, receipt: clarification } : m) })
    render(<CompanionOutcome />)
    expect(await screen.findByText(question)).toBeTruthy()
    expect(screen.getAllByText('Clarification needed').length).toBeGreaterThan(0)
    expect(screen.queryByText('Requested results checked')).toBeNull()
    expect(screen.queryByRole('link', { name: 'Download checked values and evidence', hidden: true })).toBeNull()
  })

  it('withholds a previously visible checked card while a new generation revalidates it', async () => {
    useChatStore.setState({ messages: checkedMessages() })
    render(<CompanionOutcome />)
    expect(await screen.findByText(text)).toBeTruthy()
    vi.mocked(client.get).mockImplementation(() => new Promise(() => {}))
    act(() => useChatStore.setState({ utteranceGeneration: 2 }))
    expect(screen.queryByText(text)).toBeNull()
    expect(screen.getByText('Checking saved answer')).toBeTruthy()
  })

  it('withholds a previously checked body as soon as its current response fails', async () => {
    useChatStore.setState({ messages: checkedMessages() })
    render(<CompanionOutcome />)
    expect(await screen.findByText(text)).toBeTruthy()
    act(() => useChatStore.setState({ error: { error_kind: 'assurance_publication_failed', message: 'Invalid approval' } }))
    expect(screen.getByText('Checked answer unavailable')).toBeTruthy()
    expect(screen.queryByText(text)).toBeNull()
  })
})
