// Issue 04 S5: a repeat card names the earlier job call whose outcome is
// unknown (harness/job_holds.py `repeat_card`), in Expert and Guided alike.
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { ConfirmationCard, repeatCardOf } from './ConfirmationCard'
import { useChatStore } from '../store/chatStore'
import { useUIStore } from '../store/uiStore'

vi.mock('../api/chat', () => ({ postChatConfirm: vi.fn().mockResolvedValue({ ok: true }) }))

const OPENED_AT = new Date(2026, 10, 6, 14, 2).getTime() / 1000

function setPending(repeat_card?: unknown) {
  useChatStore.setState({ sessionId: 'session-1', pending: {
    tool_use_id: 'call-1', tool_name: 'solve_queue_enqueue', args: { project_id: 'alpha' },
    safety_tier: 'execution', confirmation_token: 'token-1', ttl_seconds: 300,
    expires_at_epoch_ms: Date.now() + 300_000, repeat_card,
  } })
}

beforeEach(() => {
  useUIStore.setState({ assistantDockOpen: true, uiMode: 'expert', projectMismatch: null })
})
afterEach(cleanup)

it.each(['expert', 'guided'] as const)('names the held request in %s', (uiMode) => {
  useUIStore.setState({ uiMode })
  setPending({ hold_id: 'h1', fingerprint: 'f', tool_use_id: 't', opened: 'tool_timeout',
    opened_at: OPENED_AT, job_id: null })
  render(<ConfirmationCard />)
  const at = new Date(OPENED_AT * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  expect(screen.getByTestId('chat-confirmation-repeat').textContent).toBe(
    `This repeats the same request from ${at}, whose outcome is unknown: it may still be running. `
    + 'Approve only if you want it to run again.')
  expect((screen.getByTestId('chat-confirm-approve') as HTMLButtonElement).disabled).toBe(false)
})

it('a malformed repeat card cannot be approved', () => {
  setPending({ hold_id: 'h1', opened: 'later', opened_at: OPENED_AT })
  render(<ConfirmationCard />)
  expect(screen.queryByTestId('chat-confirmation-repeat')).toBeNull()
  expect(screen.getByRole('alert').textContent).toContain('The earlier request this repeats is unavailable')
  expect((screen.getByTestId('chat-confirm-approve') as HTMLButtonElement).disabled).toBe(true)
})

it('a card without a repeat shows no repeat line', () => {
  setPending()
  render(<ConfirmationCard />)
  expect(screen.queryByTestId('chat-confirmation-repeat')).toBeNull()
})

it('reads only the fields it shows', () => {
  expect(repeatCardOf({ hold_id: 'h', opened: 'uncertain_raise', opened_at: 1, job_id: 'fg:1' }))
    .toEqual({ hold_id: 'h', opened: 'uncertain_raise', opened_at: 1, job_id: 'fg:1' })
  expect(repeatCardOf({ hold_id: '', opened: 'tool_timeout', opened_at: 1 })).toBeNull()
  expect(repeatCardOf({ hold_id: 'h', opened: 'tool_timeout', opened_at: Number.NaN })).toBeNull()
  expect(repeatCardOf({ hold_id: 'h', opened: 'tool_timeout', opened_at: 1, job_id: 3 })).toBeNull()
})
