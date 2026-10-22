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
  expect(within(rows[1]).getAllByRole('cell').map(c => c.textContent)).toEqual(["Line 'L2'", field, '300', '400', 'MVA'])
  expect(rows[2].textContent).toContain('Follows from the change, read-only')
  expect(screen.getByTestId('chat-edit-header').textContent).toBe("Line 'L2'")
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
  expect(screen.getByTestId('chat-edit-display').textContent).toBe('stored as 150000 EUR/MWh')
  // The display unit leads; an unset old value reads in words, an unbounded one as "no limit".
  expect(screen.getAllByTestId('chat-edit-row-typed')[0].textContent).toContain('not set150EUR/kWh')
  expect(screen.getByTestId('chat-edit-row-note').textContent).toContain('Annualised by PyPSA at solve time')
  expect(screen.getByTestId('chat-edit-row-note').textContent).not.toContain('Empty')
})

it('a Settings row names the unit alone and shows the rounded value without a stored-as line', () => {
  const pending = useChatStore.getState().pending!
  const gfm = { component_class: 'Settings', name: 'BESS_NEW', field: 'tau_f_s', label: 'Frequency filter time τ_f',
    old: 0.1, new: 0.526130756579, unit: 's', role: 'typed', display: { old: 0.1, new: 0.526, unit: 's' } }
  useChatStore.setState({ pending: { ...pending, edit_card: { ...card, rows: [gfm] } } })
  render(<ConfirmationCard />)
  const row = screen.getAllByTestId('chat-edit-row-typed')[0]
  expect(row.textContent).toContain('BESS_NEW')
  expect(row.textContent).not.toMatch(/Settings|0\.5261/)
  expect(screen.getByTestId('chat-edit-header').textContent).toBe('BESS_NEW')
  expect(screen.queryByTestId('chat-edit-display')).toBeNull()
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

it('names the flow section and shows the field in Properties without leaving the chat (PR-2)', async () => {
  const pending = useChatStore.getState().pending!
  const target = { section: 'connection', panel: null, component: 'Line', entity: 'L2', field: 's_nom' }
  useChatStore.setState({ pending: { ...pending, edit_card: { ...card,
    section: { id: 'connection', label: 'Connection & grid' }, rows: [{ ...typed, target }] } } })
  useUIStore.setState({ selectedComponent: null, activeSlidePanel: null, assistantDockOpen: true })
  render(<ConfirmationCard />)
  expect(screen.getByTestId('chat-edit-header').textContent).toBe("Connection & grid \u203a Line 'L2'")
  await userEvent.click(screen.getByTestId('chat-edit-show'))
  expect(useUIStore.getState().selectedComponent).toEqual({ type: 'Line', name: 'L2' })
  expect(useUIStore.getState().activeSlidePanel).toBeNull()
  expect(useUIStore.getState().assistantDockOpen).toBe(true)
})

it('the receipt says the section state only when it changed (PR-2)', () => {
  const base = { version: 1, digest: card.digest, outcome: 'verified', says: card.says, results_stale: false,
    rows: [{ ...typed, readback: 400 }], undo: { tool: 'undo_last', available: false } }
  const { rerender } = render(<AssistantResultCard result={{ _edit_receipt: { ...base,
    section: { id: 'connection', label: 'Connection & grid', state: 'complete', changed: true } } }}
    disabled={false} onRequest={vi.fn()} />)
  expect(screen.getByTestId('chat-edit-section').textContent).toBe('Connection & grid: complete.')
  rerender(<AssistantResultCard result={{ _edit_receipt: { ...base,
    section: { id: 'connection', label: 'Connection & grid', state: 'complete', changed: false } } }}
    disabled={false} onRequest={vi.fn()} />)
  expect(screen.queryByTestId('chat-edit-section')).toBeNull()
})

it.each([['complete', 'complete'], ['warning', 'to check'], ['incomplete', 'to do'], ['not_needed', 'not needed'],
  ['unknown', 'not known yet']])('the receipt words section state %s as the rail does', (state, words) => {
  const receipt = { version: 1, digest: card.digest, outcome: 'verified', says: card.says, results_stale: false,
    rows: [{ ...typed, readback: 400 }], undo: { tool: 'undo_last', available: false },
    section: { id: 'assets', label: 'Assets & sizing', state, changed: true } }
  render(<AssistantResultCard result={{ _edit_receipt: receipt }} disabled={false} onRequest={vi.fn()} />)
  expect(screen.getByTestId('chat-edit-section').textContent).toBe(`Assets & sizing: ${words}.`)
})

it('an unbounded old value reads as "no limit", never "inf"', () => {
  const pending = useChatStore.getState().pending!
  useChatStore.setState({ pending: { ...pending, edit_card: { ...card, rows: [{ ...typed, old: 'inf' }] } } })
  render(<ConfirmationCard />)
  const row = screen.getAllByTestId('chat-edit-row-typed')[0].textContent!
  expect(row).toContain('no limit')
  expect(row).not.toContain('inf')
})

it('a table-sourced row names its file, sheet and cell, and a cost its money basis', () => {
  const pending = useChatStore.getState().pending!
  const sourced = { ...typed, source: { filename: 'lines.csv', sheet: '(csv)', cell: 'B3', raw: '400', raw_unit: 'MVA' },
    money: { currency: 'EUR', money_year: 2025 } }
  useChatStore.setState({ pending: { ...pending, tool_name: 'apply_equipment_mapping',
    edit_card: { ...card, tool: 'apply_equipment_mapping', rows: [sourced] } } })
  render(<ConfirmationCard />)
  expect(screen.getByTestId('chat-edit-source').textContent).toBe('lines.csv \u203a B3 (400 MVA)')
  expect(screen.getByTestId('chat-edit-money').textContent).toBe('EUR, 2025 money, not adjusted for inflation')
})

it('a PDF-sourced row names its file, page and quote, a long quote clipped with the full text in the tooltip', () => {
  const pending = useChatStore.getState().pending!
  const short = { ...typed, source: { filename: 'datasheet.pdf', page: 4, quote: 'Rated power 2 500 kW',
    raw: '2 500', raw_unit: 'kW' } }
  const long = 'Rated continuous AC output power at 25 C ambient and nominal voltage, per IEC 62109: 2 500 kW'
  useChatStore.setState({ pending: { ...pending, tool_name: 'apply_document_values',
    edit_card: { ...card, tool: 'apply_document_values', rows: [short, { ...short, source: { ...short.source, quote: long } }] } } })
  render(<ConfirmationCard />)
  const [first, second] = screen.getAllByTestId('chat-edit-source')
  expect(first.textContent).toBe('datasheet.pdf \u203a p. 4 \u00b7 \u201cRated power 2 500 kW\u201d (2 500 kW)')
  expect(second.textContent).toContain('\u2026\u201d (2 500 kW)')
  expect(second.textContent).not.toContain(long)
  expect(second.getAttribute('title')).toBe(long)
})

it('a PDF-sourced row with no quote names its page alone', () => {
  const pending = useChatStore.getState().pending!
  const bare = { ...typed, source: { filename: 'datasheet.pdf', page: 4, quote: '', raw: '2 500', raw_unit: 'kW' } }
  useChatStore.setState({ pending: { ...pending, tool_name: 'apply_document_values',
    edit_card: { ...card, tool: 'apply_document_values', rows: [bare] } } })
  render(<ConfirmationCard />)
  expect(screen.getByTestId('chat-edit-source').textContent).toBe('datasheet.pdf \u203a p. 4 (2 500 kW)')
})

it('a unitless table cell shows its raw value without a stray space', () => {
  const pending = useChatStore.getState().pending!
  const sourced = { ...typed, source: { filename: 'lines.csv', sheet: '(csv)', cell: 'D2', raw: '3', raw_unit: '' } }
  useChatStore.setState({ pending: { ...pending, tool_name: 'apply_equipment_mapping',
    edit_card: { ...card, tool: 'apply_equipment_mapping', rows: [sourced] } } })
  render(<ConfirmationCard />)
  expect(screen.getByTestId('chat-edit-source').textContent).toBe('lines.csv \u203a D2 (3)')
})

it('a workbook row names its sheet, and a formula cell says its value was saved', () => {
  const pending = useChatStore.getState().pending!
  const sourced = { ...typed, source: { filename: 'lines.xlsx', sheet: 'Lines', cell: 'B3', raw: 450, raw_unit: 'MVA',
    formula: true } }
  useChatStore.setState({ pending: { ...pending, tool_name: 'apply_equipment_mapping',
    edit_card: { ...card, tool: 'apply_equipment_mapping', rows: [sourced] } } })
  render(<ConfirmationCard />)
  expect(screen.getByTestId('chat-edit-source').textContent)
    .toBe('lines.xlsx \u203a Lines \u203a B3 (450 MVA, saved formula value)')
})
