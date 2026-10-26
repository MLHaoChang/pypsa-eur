import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ConfirmationCard } from './ConfirmationCard'
import { useChatStore } from '../store/chatStore'
import { useUIStore } from '../store/uiStore'
import { postChatConfirm } from '../api/chat'

vi.mock('../api/chat', () => ({ postChatConfirm: vi.fn().mockResolvedValue({ ok: true }) }))

const args = {
  preview_id: 'preview-1', project_id: 'project-1', review_sha256: 'a'.repeat(64),
  changes_total: 1, reviewed_changes: [{ component_class: 'Generator', name: 'G1',
    attribute: 'marginal_cost', before: 10, after: 20, unit: 'currency/MWh' }],
}

beforeEach(() => {
  vi.clearAllMocks()
  useUIStore.setState({ assistantDockOpen: true, uiMode: 'expert', projectMismatch: null })
  useChatStore.setState({ sessionId: 'session-1', pending: {
    tool_use_id: 'call-1', tool_name: 'apply_project_changes', args,
    safety_tier: 'execution', confirmation_token: 'token-1', ttl_seconds: 300,
    expires_at_epoch_ms: Date.now() + 300_000,
  } })
})
afterEach(cleanup)

it.each([[true, 'expert'], [false, 'expert'], [true, 'guided'], [false, 'guided']] as const)(
  'shows the exact complete review with dock open=%s in %s', async (open, uiMode) => {
  useUIStore.setState({ assistantDockOpen: open, uiMode })
  render(<ConfirmationCard />)
  const table = screen.getByRole('table', { name: 'Changes to apply' })
  const row = within(table).getAllByRole('row')[1]
  expect(within(row).getAllByRole('cell').map(c => c.textContent)).toEqual([
    'Generator G1', 'marginal_cost', '10', '20', 'currency/MWh',
  ])
  await userEvent.click(screen.getByRole('button', { name: 'Approve' }))
  expect(postChatConfirm).toHaveBeenCalledWith('session-1', { token: 'token-1', decision: 'approve' })
})

it.each([
  { changes_total: 2 }, { reviewed_changes: [] }, { diff_truncated: true },
  { review_sha256: undefined }, { reviewed_changes: [{ ...args.reviewed_changes[0], unit: null }] },
])('blocks approval of an incomplete preview but preserves denial: %s', async (invalid) => {
  const pending = useChatStore.getState().pending!
  useChatStore.setState({ pending: { ...pending, args: { ...args, ...invalid } } })
  render(<ConfirmationCard />)
  expect(screen.getByRole('alert').textContent).toContain('complete change preview is unavailable')
  const approve = screen.getByRole('button', { name: 'Approve' }) as HTMLButtonElement
  expect(approve.disabled).toBe(true)
  await userEvent.click(approve)
  expect(postChatConfirm).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button', { name: 'Deny' }))
  expect(postChatConfirm).toHaveBeenCalledWith('session-1', { token: 'token-1', decision: 'deny' })
})

it('preserves zero and boolean values without substituting a missing value', () => {
  const pending = useChatStore.getState().pending!
  useChatStore.setState({ pending: { ...pending, args: { ...args,
    reviewed_changes: [{ ...args.reviewed_changes[0], before: false, after: true, unit: '' },
      { ...args.reviewed_changes[0], before: 0, after: 0.001 }], changes_total: 2 } } })
  render(<ConfirmationCard />)
  const rows = within(screen.getByRole('table')).getAllByRole('row')
  expect(within(rows[1]).getAllByRole('cell').map(c => c.textContent)).toEqual([
    'Generator G1', 'marginal_cost', 'false', 'true', 'No unit',
  ])
  expect(within(rows[2]).getAllByRole('cell').map(c => c.textContent)).toEqual([
    'Generator G1', 'marginal_cost', '0', '0.001', 'currency/MWh',
  ])
})

it('shows several assets once, in the edit card, when the apply carries one (LC-16)', () => {
  const row = (name: string, old: number, now: number) => ({ component_class: 'Line', name, field: 's_nom',
    label: 'Rating', old, new: now, unit: 'MVA', role: 'typed' })
  const edit_card = { version: 1, kind: 'parameter_edit', tool: 'apply_project_changes', complete: true,
    says: 'L1 s_nom 300 → 500 MVA; L2 s_nom 300 → 450 MVA', digest: 'd'.repeat(64),
    rows: [row('L1', 300, 500), row('L2', 300, 450)] }
  useChatStore.setState({ pending: { ...useChatStore.getState().pending!, edit_card } })
  render(<ConfirmationCard />)
  // One table: the edit card's rows, not the bare diff beside it.
  const tables = screen.getAllByRole('table', { name: 'Changes to apply' })
  expect(tables).toHaveLength(1)
  expect(within(tables[0]).getAllByRole('row')).toHaveLength(3)
  expect(within(tables[0]).queryByText('Generator G1')).toBeNull()
  expect((screen.getByRole('button', { name: 'Approve' }) as HTMLButtonElement).disabled).toBe(false)
})
