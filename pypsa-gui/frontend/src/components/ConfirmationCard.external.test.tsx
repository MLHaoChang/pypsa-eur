// Issue 19 slice 2 — a card for a tool from an admin's MCP server says so,
// in Expert and Guided alike (external non-read tools are carded in both).
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { ConfirmationCard } from './ConfirmationCard'
import { useChatStore } from '../store/chatStore'
import { useUIStore } from '../store/uiStore'

vi.mock('../api/chat', () => ({ postChatConfirm: vi.fn().mockResolvedValue({ ok: true }) }))

function setPending(external?: { server: string }) {
  useChatStore.setState({ sessionId: 'session-1', pending: {
    tool_use_id: 'call-1', tool_name: 'mcp__tariffs__upload_tariff', args: { id: 'x' },
    safety_tier: 'write', confirmation_token: 'token-1', ttl_seconds: 300,
    expires_at_epoch_ms: Date.now() + 300_000, external,
  } })
}

beforeEach(() => {
  useUIStore.setState({ assistantDockOpen: true, uiMode: 'expert', projectMismatch: null })
})
afterEach(cleanup)

it.each(['expert', 'guided'] as const)('labels an external tool in %s', (uiMode) => {
  useUIStore.setState({ uiMode })
  setPending({ server: 'tariffs' })
  render(<ConfirmationCard />)
  expect(screen.getByTestId('chat-confirmation-external').textContent)
    .toBe('External tool from MCP server “tariffs”')
})

it('leaves a built-in tool unlabelled', () => {
  setPending()
  render(<ConfirmationCard />)
  expect(screen.queryByTestId('chat-confirmation-external')).toBeNull()
})
