// Study harness 10a (UX (14)): the campus study's card is issued by the run's
// runner, not the assistant: Expert titles it with the edit card's own words,
// and a card of note rows only shows no empty column header.
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { ConfirmationCard } from './ConfirmationCard'
import { useChatStore } from '../store/chatStore'
import { useUIStore } from '../store/uiStore'

vi.mock('../api/chat', () => ({ postChatConfirm: vi.fn().mockResolvedValue({ ok: true }) }))

const note = (field: string, text: string) => ({ role: 'note', field, unit: '', note: text })
const CARD = {
  version: 1, kind: 'native', tool: 'dc_campus_study', complete: true, digest: 'd'.repeat(64), sets: [],
  says: 'Run the campus study held to SAGC data centre',
  rows: [note('grid_code', 'Grid-code profile: SAGC data centre'), note('solves', 'Reserves 1 solve')],
}

function setPending(edit_card: unknown) {
  useChatStore.setState({ sessionId: 'session-1', pending: {
    tool_use_id: 'native-solve.3', tool_name: 'dc_campus_study', args: { digest: 'd'.repeat(64) },
    safety_tier: 'write', confirmation_token: 'token-1', ttl_seconds: 300,
    expires_at_epoch_ms: Date.now() + 300_000, edit_card, session_id: 'run:t',
  } })
}

beforeEach(() => {
  useUIStore.setState({ assistantDockOpen: true, uiMode: 'expert', projectMismatch: null })
})
afterEach(cleanup)

it.each(['expert', 'guided'] as const)('titles a native card with its own words in %s', (uiMode) => {
  useUIStore.setState({ uiMode })
  setPending(CARD)
  render(<ConfirmationCard />)
  const title = document.getElementById('chat-confirmation-title')!.textContent
  expect(title).toBe('Run the campus study held to SAGC data centre')
  expect(title).not.toContain('assistant')
})

it('keeps the generic Expert title for an edit card that is not native', () => {
  setPending({ ...CARD, kind: 'edit' })
  render(<ConfirmationCard />)
  expect(document.getElementById('chat-confirmation-title')!.textContent).toBe('The assistant wants to use dc campus study')
})

it('shows no column header over note rows only, and the notes', () => {
  setPending(CARD)
  render(<ConfirmationCard />)
  expect(screen.queryByRole('columnheader')).toBeNull()
  expect(screen.getAllByTestId('chat-edit-card-note').map(n => n.textContent))
    .toEqual(['Grid-code profile: SAGC data centre', 'Reserves 1 solve'])
})
