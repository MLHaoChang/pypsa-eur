import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ConfirmationCard } from './ConfirmationCard'
import AssistantResultCard from './AssistantResultCard'
import { useChatStore } from '../store/chatStore'
import { useUIStore } from '../store/uiStore'
import { postChatConfirm } from '../api/chat'

vi.mock('../api/chat', () => ({ postChatConfirm: vi.fn().mockResolvedValue({ ok: true }) }))

const typed = { component_class: 'Line', name: 'L2', field: 's_nom', label: 'Rating', old: 300, new: 400,
  unit: 'MVA', role: 'typed' }
const card = { version: 1, kind: 'parameter_edit', tool: 'update_component', complete: true,
  says: 'L2 s_nom 300 → 400 MVA', digest: 'd'.repeat(64), rows: [typed, { component_class: 'Line', name: 'L2',
    field: 'capital_cost', label: 'Capital cost', old: 10, new: 12, unit: 'EUR/MVA/yr', role: 'derived' }] }

beforeEach(() => {
  vi.clearAllMocks()
  useUIStore.setState({ assistantDockOpen: true, uiMode: 'expert', projectMismatch: null })
  useChatStore.setState({ sessionId: 'session-1', pending: {
    tool_use_id: 'call-1', tool_name: 'update_component',
    args: { component_class: 'Line', name: 'L2', attrs: { s_nom: 400 } }, safety_tier: 'write',
    confirmation_token: 'token-1', ttl_seconds: 300, expires_at_epoch_ms: Date.now() + 300_000, edit_card: card,
  } })
})
afterEach(cleanup)

it.each([['expert', 's_nom'], ['guided', 'Rating']] as const)('shows old → new in %s mode', async (uiMode, field) => {
  useUIStore.setState({ uiMode })
  render(<ConfirmationCard />)
  const rows = within(screen.getByRole('table', { name: 'Changes to apply' })).getAllByRole('row')
  expect(within(rows[1]).getAllByRole('cell').map(c => c.textContent)).toEqual(['Line L2', field, '300', '400', 'MVA'])
  expect(rows[2].textContent).toContain('Follows from the change, read-only')
  expect(screen.getByTestId('chat-edit-header').textContent).toBe('Line > L2')
  await userEvent.click(screen.getByRole('button', { name: 'Approve' }))
  expect(postChatConfirm).toHaveBeenCalledWith('session-1', { token: 'token-1', decision: 'approve' })
})

it('a malformed card cannot be approved but can be denied', async () => {
  const pending = useChatStore.getState().pending!
  useChatStore.setState({ pending: { ...pending, edit_card: { ...card, rows: [] } } })
  render(<ConfirmationCard />)
  expect(screen.getByRole('alert').textContent).toContain('old and new values of this change are unavailable')
  expect((screen.getByRole('button', { name: 'Approve' }) as HTMLButtonElement).disabled).toBe(true)
  await userEvent.click(screen.getByRole('button', { name: 'Deny' }))
  expect(postChatConfirm).toHaveBeenCalledWith('session-1', { token: 'token-1', decision: 'deny' })
})

it('the receipt says read back, stale results and offers undo', async () => {
  const onRequest = vi.fn()
  const receipt = { version: 1, digest: card.digest, outcome: 'verified', says: card.says, results_stale: true,
    rows: [{ ...typed, readback: 400 }], undo: { tool: 'undo_last', available: true } }
  render(<AssistantResultCard result={{ name: 'L2', _edit_receipt: receipt }} disabled={false} onRequest={onRequest} />)
  expect(screen.getByTestId('chat-edit-receipt').textContent).toContain('L2 s_nom 300 → 400 MVA. Read back')
  expect(screen.getByTestId('chat-edit-stale')).toBeTruthy()
  await userEvent.click(screen.getByTestId('chat-edit-undo'))
  expect(onRequest).toHaveBeenCalledWith('Undo that change with undo_last.')
})

it('an uncertain receipt never reads as success', () => {
  const receipt = { version: 1, digest: card.digest, outcome: 'uncertain', says: card.says, results_stale: false,
    rows: [{ ...typed, readback: 300 }], undo: { tool: 'undo_last', available: false } }
  render(<AssistantResultCard result={{ _edit_receipt: receipt }} disabled={false} onRequest={vi.fn()} />)
  expect(screen.getByTestId('chat-edit-receipt').textContent).toContain('does not show the expected values')
  expect(screen.queryByTestId('chat-edit-undo')).toBeNull()
})

it('a note row states the rule without inventing values', () => {
  const pending = useChatStore.getState().pending!
  const note = { component_class: 'Store', name: 'H2 tank', field: 'overnight_cost', label: 'overnight_cost',
    old: null, new: null, unit: '', role: 'note', note: 'Annualised by PyPSA at solve time: overnight_cost × annuity' }
  const stored = { ...typed, component_class: 'Store', name: 'H2 tank', field: 'overnight_cost', old: null,
    new: 150000, unit: 'EUR/MWh', display: { old: null, new: 150, unit: 'EUR/kWh' } }
  useChatStore.setState({ pending: { ...pending, edit_card: { ...card, rows: [stored, note] } } })
  render(<ConfirmationCard />)
  expect(screen.getByTestId('chat-edit-display').textContent).toBe('150 EUR/kWh = 150000 EUR/MWh stored')
  expect(screen.getByTestId('chat-edit-row-note').textContent).toContain('Annualised by PyPSA at solve time')
  expect(screen.getByTestId('chat-edit-row-note').textContent).not.toContain('Empty')
})

it('a component the change creates reads as created, not as true/false', () => {
  const pending = useChatStore.getState().pending!
  const created = { component_class: 'Carrier', name: 'AC', field: 'exists', label: 'exists', old: false, new: true,
    unit: '', role: 'follows' }
  useChatStore.setState({ pending: { ...pending, edit_card: { ...card, rows: [typed, created] } } })
  render(<ConfirmationCard />)
  const row = screen.getAllByTestId('chat-edit-row-follows')[0]
  expect(row.textContent).toContain('Created by this change')
  expect(row.textContent).toContain('absent')
  expect(row.textContent).toContain('present')
  expect(row.textContent).not.toContain('true')
})
