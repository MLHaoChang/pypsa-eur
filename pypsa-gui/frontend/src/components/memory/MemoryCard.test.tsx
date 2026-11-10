// Assistant memory 02b (plan §2.2): `propose_memory`'s card is the memory card,
// in Guided and Expert alike. Remember approves; Edit first approves and opens
// the saved item's edit form; Not now denies AND records a dismissal.
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ConfirmationCard } from '../ConfirmationCard'
import { useChatStore } from '../../store/chatStore'
import { useUIStore } from '../../store/uiStore'
import { useMemoryStore } from '../../store/memoryStore'
import { postChatConfirm } from '../../api/chat'
import { memoryApi } from '../../api/memory'
import { memoryProposalOf } from './MemoryCard'

vi.mock('../../api/chat', () => ({ postChatConfirm: vi.fn().mockResolvedValue({ ok: true }) }))
vi.mock('../../api/memory', async (orig) => {
  const real = await orig<typeof import('../../api/memory')>()
  return { ...real, memoryApi: { ...real.memoryApi, dismiss: vi.fn().mockResolvedValue({}) } }
})

const ARGS = {
  kind: 'preference',
  summary: 'Show power in GW',
  body: { key: 'units.power', value: 'GW' },
  concept_keys: ['topic:units', 'view:asset'],
  project_kind: 'capacity_expansion',
}

function setPending(args: Record<string, unknown> = ARGS) {
  useChatStore.setState({ sessionId: 'session-1', messages: [], pending: {
    tool_use_id: 'call-m', tool_name: 'propose_memory', args,
    safety_tier: 'write', confirmation_token: 'token-m', ttl_seconds: 300,
    expires_at_epoch_ms: Date.now() + 300_000,
  } })
}

beforeEach(() => {
  vi.mocked(postChatConfirm).mockClear()
  vi.mocked(memoryApi.dismiss).mockClear()
  useUIStore.setState({ assistantDockOpen: true, uiMode: 'expert', projectMismatch: null })
  useMemoryStore.setState({ open: false, editId: null, tab: 'items' })
})
afterEach(cleanup)

it.each(['expert', 'guided'] as const)('shows the proposal in plain words in %s', (uiMode) => {
  useUIStore.setState({ uiMode })
  setPending()
  render(<ConfirmationCard />)
  expect(screen.getByTestId('chat-confirmation-header').textContent).toBe('Remember this?')
  expect(screen.getByTestId('memory-card-kind').textContent).toBe('Preference')
  expect(screen.getByTestId('memory-card-summary').textContent).toBe('Show power in GW')
  expect(screen.getByTestId('memory-card-scope').textContent)
    .toBe('Just me · Capacity expansion projects')
  expect(screen.getByText('Asset detail')).toBeTruthy()
  // The generic Approve/Deny pair is replaced, not duplicated.
  expect(screen.queryByTestId('chat-confirm-approve')).toBeNull()
  expect(screen.queryByText('Approve')).toBeNull()
})

it('Remember approves the card and records nothing else', async () => {
  setPending()
  render(<ConfirmationCard />)
  fireEvent.click(screen.getByTestId('memory-card-remember'))
  await waitFor(() => expect(postChatConfirm).toHaveBeenCalledWith('session-1',
    { token: 'token-m', decision: 'approve' }))
  expect(memoryApi.dismiss).not.toHaveBeenCalled()
})

it('Not now denies the card and records the dismissal', async () => {
  setPending()
  render(<ConfirmationCard />)
  fireEvent.click(screen.getByTestId('memory-card-not-now'))
  await waitFor(() => expect(memoryApi.dismiss).toHaveBeenCalledWith({
    kind: 'preference', body: ARGS.body, concept_keys: ARGS.concept_keys,
    project_kind: 'capacity_expansion', summary: 'Show power in GW',
  }))
  expect(postChatConfirm).toHaveBeenCalledWith('session-1', { token: 'token-m', decision: 'deny' })
})

it('Not now records no dismissal when the deny itself failed', async () => {
  vi.mocked(postChatConfirm).mockRejectedValueOnce(new Error('gone'))
  setPending()
  render(<ConfirmationCard />)
  fireEvent.click(screen.getByTestId('memory-card-not-now'))
  await waitFor(() => expect(postChatConfirm).toHaveBeenCalled())
  expect(memoryApi.dismiss).not.toHaveBeenCalled()
})

it('Edit first approves, then opens the saved item in the Memory dialog', async () => {
  setPending()
  render(<ConfirmationCard />)
  fireEvent.click(screen.getByTestId('memory-card-edit'))
  await waitFor(() => expect(postChatConfirm).toHaveBeenCalledWith('session-1',
    { token: 'token-m', decision: 'approve' }))
  useChatStore.getState().appendMessage({
    role: 'tool', content: '✓ propose_memory', tool_use_id: 'call-m',
    tool_name: 'propose_memory', tool_result: { id: 'mem_abc', status: 'confirmed' },
  })
  await waitFor(() => expect(useMemoryStore.getState().open).toBe(true))
  expect(useMemoryStore.getState().editId).toBe('mem_abc')
})

it('a project mismatch blocks Remember and Edit first, not Not now', () => {
  useUIStore.setState({ projectMismatch: { tab: 'a', backend: 'b' } as never })
  setPending()
  render(<ConfirmationCard />)
  expect((screen.getByTestId('memory-card-remember') as HTMLButtonElement).disabled).toBe(true)
  expect((screen.getByTestId('memory-card-edit') as HTMLButtonElement).disabled).toBe(true)
  expect((screen.getByTestId('memory-card-not-now') as HTMLButtonElement).disabled).toBe(false)
})

it('args that are not a proposal fall back to the plain card', () => {
  expect(memoryProposalOf({ kind: 'preference', summary: 1 })).toBeNull()
  expect(memoryProposalOf({ ...ARGS, kind: 'secret' })).toBeNull()
  setPending({ kind: 'preference' })
  render(<ConfirmationCard />)
  expect(screen.queryByTestId('memory-card')).toBeNull()
  expect(screen.getByTestId('chat-confirm-approve')).toBeTruthy()
})
