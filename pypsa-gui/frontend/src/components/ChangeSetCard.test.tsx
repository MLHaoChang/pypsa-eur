// LC-47: one change-set card. A tick box per proposed change, one approval for
// the ticked ones, and one receipt in words.
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ConfirmationCard } from './ConfirmationCard'
import { CHANGE_SET_NONE_TICKED, ChangeSetReceiptCard, changeSetCountLine, changeSetOf, changeSetReceiptOf,
  changeSetReceiptText, type ChangeSetReceipt } from './ChangeSetCard'
import { InvalidDecisionError, postChatConfirm } from '../api/chat'
import { useChatStore } from '../store/chatStore'
import { useUIStore } from '../store/uiStore'

vi.mock('../api/chat', async importOriginal => ({
  ...await importOriginal<typeof import('../api/chat')>(),
  postChatConfirm: vi.fn().mockResolvedValue({ ok: true }),
}))

const card = (name: string, field: string, label: string, now: number | boolean) => ({
  version: 1, kind: 'edit', tool: 'update_component', complete: true, digest: name.repeat(8), sets: [],
  says: `Set Bus ${name} ${field} to ${now}`,
  rows: [{ component_class: 'Bus', name, field, label, old: typeof now === 'number' ? 0 : false, new: now,
    unit: typeof now === 'number' ? 'MVA' : '', role: 'typed' }],
})
const item = (id: string, name: string, field: string, label: string, now: number | boolean, source = 'proposed by the assistant') => ({
  tool_use_id: id, tool: 'update_component', edit_card: card(name, field, label, now), source, digest: id.repeat(4),
})
const SET = {
  version: 1, digest: 's'.repeat(64),
  items: [
    item('tu-0', 'POC', 'eh_poc', 'Point of connection', true),
    item('tu-1', 'POC2', 'eh_sk_mva', 'Short-circuit power', 2500),
    item('tu-2', 'IT', 'eh_critical', 'Critical load', true, 'rows'),
  ],
  requirements: { checked: 2, says: 'These changes were checked together against the project’s 2 confirmed requirements.' },
}

function setPending(change_set: unknown) {
  useChatStore.setState({ sessionId: 'session-1', messages: [], error: null, pending: {
    tool_use_id: 'tu-0', tool_name: 'change_set', args: { tool_use_ids: ['tu-0', 'tu-1', 'tu-2'] },
    safety_tier: 'write', confirmation_token: 'token-1', ttl_seconds: 300,
    expires_at_epoch_ms: Date.now() + 300_000, change_set,
  } })
}
const ticks = () => screen.getAllByTestId('chat-change-set-tick') as HTMLInputElement[]
const approve = () => screen.getByTestId('chat-confirm-approve') as HTMLButtonElement

beforeEach(() => {
  vi.mocked(postChatConfirm).mockReset().mockResolvedValue({ ok: true })
  useUIStore.setState({ assistantDockOpen: true, uiMode: 'guided', projectMismatch: null })
})
afterEach(cleanup)

it.each(['guided', 'expert'] as const)('lists every change with a tick box and the same labels in %s', (uiMode) => {
  useUIStore.setState({ uiMode })
  setPending(SET)
  render(<ConfirmationCard />)
  expect(document.getElementById('chat-confirmation-title')!.textContent).toBe('3 changes proposed for this step')
  expect(ticks().map(t => t.checked)).toEqual([true, true, true])
  expect(ticks().map(t => document.querySelector(`label[for="${t.id}"]`)!.textContent))
    .toEqual(['Bus “POC”', 'Bus “POC2”', 'Bus “IT”'])
  // UX C5: the panel's field labels, never the raw field, in both modes.
  const table = screen.getByTestId('chat-change-set').textContent!
  expect(table).toContain('Short-circuit power')
  expect(table).not.toContain('eh_sk_mva')
  // K9: a source line only where the rows carry none of their own.
  expect(screen.getAllByTestId('chat-change-set-source').map(s => s.textContent))
    .toEqual(['Source: proposed by the assistant', 'Source: proposed by the assistant'])
  expect(screen.getByTestId('chat-change-set-requirements').textContent).toBe(SET.requirements.says)
  expect(screen.getByTestId('chat-change-set-count').textContent).toBe('3 of 3 ticked')
  expect(approve().textContent).toBe('Apply 3 changes')
  expect(screen.getByTestId('chat-confirm-deny').textContent).toBe('Cancel all')
  // UX (16): the rows are the detail; no arguments block, no Details, no tool-use ids.
  expect(screen.queryByTestId('chat-confirmation-details')).toBeNull()
  expect(screen.getByTestId('chat-confirmation-card').querySelector('pre')).toBeNull()
  expect(screen.getByTestId('chat-confirmation-card').textContent).not.toContain('tu-0')
})

it('applies exactly the ticked rows, in card order', async () => {
  setPending(SET)
  render(<ConfirmationCard />)
  fireEvent.click(ticks()[1])
  expect(screen.getByTestId('chat-change-set-count').textContent).toBe('2 of 3 ticked')
  expect(approve().textContent).toBe('Apply 2 changes')
  fireEvent.click(approve())
  await waitFor(() => expect(postChatConfirm).toHaveBeenCalledWith('session-1',
    { token: 'token-1', decision: 'approve', ticked: ['tu-0', 'tu-2'] }))
  await waitFor(() => expect(useChatStore.getState().pending).toBeNull())
})

it('says why Apply is off when no row is ticked, and sends nothing', () => {
  setPending(SET)
  render(<ConfirmationCard />)
  ticks().forEach(t => fireEvent.click(t))
  expect(approve().disabled).toBe(true)
  expect(approve().textContent).toBe('Apply 0 changes')
  expect(screen.getByTestId('chat-change-set-none').textContent).toBe(CHANGE_SET_NONE_TICKED)
  fireEvent.click(approve())
  expect(postChatConfirm).not.toHaveBeenCalled()
  // ticking one again brings Apply back
  fireEvent.click(ticks()[2])
  expect(approve().disabled).toBe(false)
  expect(screen.queryByTestId('chat-change-set-none')).toBeNull()
})

it('Cancel all denies the whole set and says nothing changed', async () => {
  setPending(SET)
  render(<ConfirmationCard />)
  fireEvent.click(screen.getByTestId('chat-confirm-deny'))
  await waitFor(() => expect(postChatConfirm).toHaveBeenCalledWith('session-1', { token: 'token-1', decision: 'deny' }))
  await waitFor(() => expect(useChatStore.getState().pending).toBeNull())
  expect(useChatStore.getState().messages.map(m => m.content)).toEqual(['Cancelled: nothing changed'])
})

it('a card left to time out is withdrawn and says nothing changed, with no error banner', () => {
  vi.useFakeTimers()
  try {
    setPending(SET)
    useChatStore.setState({ pending: { ...useChatStore.getState().pending!, expires_at_epoch_ms: Date.now() + 1000 } })
    render(<ConfirmationCard />)
    act(() => { vi.advanceTimersByTime(1500) })
    expect(useChatStore.getState().pending).toBeNull()
    expect(useChatStore.getState().messages.map(m => m.content)).toEqual(['Timed out: nothing changed'])
    expect(useChatStore.getState().error).toBeNull()
  } finally { vi.useRealTimers() }
})

it('keeps the card and says so when the server refuses the ticks', async () => {
  vi.mocked(postChatConfirm).mockRejectedValue(new InvalidDecisionError('ticked must list rows of this card'))
  setPending(SET)
  render(<ConfirmationCard />)
  fireEvent.click(approve())
  expect((await screen.findByTestId('chat-change-set-refused')).textContent)
    .toBe('These ticks no longer match the card, so nothing was applied. Cancel all and ask again.')
  expect(useChatStore.getState().pending?.confirmation_token).toBe('token-1')
})

it.each([
  ['one item only', { ...SET, items: SET.items.slice(0, 1) }],
  ['a repeated id', { ...SET, items: [SET.items[0], SET.items[0]] }],
  ['a malformed edit card', { ...SET, items: [SET.items[0], { ...SET.items[1], edit_card: { rows: [] } }] }],
  ['no requirements line', { ...SET, requirements: {} }],
  ['no rows at all', undefined],
])('never offers Apply for a malformed set: %s', (_name, broken) => {
  expect(changeSetOf(broken)).toBeNull()
  setPending(broken)
  render(<ConfirmationCard />)
  expect(approve().disabled).toBe(true)
  expect(screen.getByRole('alert').textContent).toBe('The changes of this card are unavailable. Cancel all and ask again.')
  expect(screen.queryByTestId('chat-change-set')).toBeNull()
})

const row = (id: string, status: string, error_kind: string | null = null) =>
  ({ tool_use_id: id, tool: 'update_component', status, error_kind, label: `backend ${id}` })
const receipt = (decision: string, rows: ReturnType<typeof row>[], ticked: number, landed: number, failed: number) =>
  changeSetReceiptOf({ version: 1, decision, rows, ticked, landed, failed, all_applied: ticked > 0 && landed === ticked }) as ChangeSetReceipt

it('words each row of the receipt and keeps a failed row’s reason on its row', () => {
  const r = receipt('approve', [row('a', 'applied'), row('b', 'failed', 'validation_error'), row('c', 'unticked', 'change_unticked'),
    row('d', 'uncertain'), row('e', 'stale', 'edit_preview_stale'), row('f', 'unchanged'),
    row('g', 'not_run', 'tool_call_cap_exceeded')], 6, 1, 2)
  render(<ChangeSetReceiptCard receipt={r} labels={{ a: 'Bus “POC”' }} reasons={{ b: 'Short-circuit power must be above 0' }} />)
  const count = screen.getByTestId('chat-change-set-receipt-count')
  expect(count.getAttribute('role')).toBe('status')
  expect(count.textContent).toBe('1 of 6 applied · 2 failed · 1 not confirmed yet · 1 not run · 1 already had the value')
  expect(screen.getAllByTestId('chat-change-set-receipt-row').map(li => li.textContent)).toEqual([
    'Bus “POC”Applied',
    'backend bFailed: Short-circuit power must be above 0',
    'backend cNot applied (unticked)',
    'backend dApplied: not confirmed yet',
    'backend eFailed: the project changed after the card was shown',
    'backend fNo change: the project already had this value',
    // GK B (44) F1: the turn ended first; nothing was written, so it is not a failure.
    'backend gNot applied: the turn ended before this change ran',
  ])
  expect(screen.getAllByTestId('chat-change-set-receipt-row')[6].querySelector('.text-warn')).toBeNull()
})

it('counts what landed out of what was ticked', () => {
  expect(changeSetCountLine(receipt('approve', [row('a', 'applied'), row('b', 'applied'), row('c', 'applied'),
    row('d', 'failed', 'x')], 4, 3, 1))).toBe('3 of 4 applied · 1 failed')
  expect(changeSetCountLine(receipt('approve', [row('a', 'applied'), row('b', 'unticked')], 1, 1, 0))).toBe('1 of 1 applied')
})

it.each([
  ['deny', 'Cancelled: nothing changed'],
  ['expired', 'Timed out: nothing changed'],
  ['aborted', 'Stopped: nothing changed'],
  ['revoked', 'Not applied: the rules for confirming changed while the card was open. Nothing changed'],
  ['unavailable', 'Not applied: the app could not record the approval. Nothing changed'],
  ['something new', 'Not applied: the app could not record the approval. Nothing changed'],
])('a set settled by %s says one true line and lists no row as applied', (decision, words) => {
  const r = receipt(decision, [row('a', 'failed', 'confirmation_denied'), row('b', 'failed', 'confirmation_denied')], 0, 0, 2)
  expect(changeSetReceiptText(r)).toBe(words)
  render(<ChangeSetReceiptCard receipt={r} />)
  const line = screen.getByTestId('chat-change-set-settled')
  expect(line.getAttribute('role')).toBe('status')
  expect(line.textContent).toBe(`${words}.`)
  expect(screen.queryByTestId('chat-change-set-receipt-row')).toBeNull()
})

it('reads no receipt from a malformed frame', () => {
  expect(changeSetReceiptOf({ version: 1, decision: 'approve', rows: [{ tool_use_id: 'a', status: 'landed' }],
    ticked: 1, landed: 1, failed: 0 })).toBeNull()
  expect(changeSetReceiptOf({ version: 2, decision: 'approve', rows: [], ticked: 0, landed: 0, failed: 0 })).toBeNull()
})
