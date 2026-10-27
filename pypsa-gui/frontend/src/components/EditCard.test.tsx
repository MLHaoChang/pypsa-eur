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

it.each([['expert', 'full', 'rolling'], ['guided', 'Full LP', 'Rolling horizon']] as const)(
  'a solver card (OJ-44) in %s mode: values, a "—" old value that did not apply, the note under the table',
  (uiMode, oldWord, newWord) => {
    useUIStore.setState({ uiMode })
    const pending = useChatStore.getState().pending!
    const strategy = { component_class: 'Settings', name: 'Solver settings', field: 'solve_strategy',
      label: 'Solve strategy', old: 'full', new: 'rolling', unit: '', role: 'typed',
      words: { old: 'Full LP', new: 'Rolling horizon' } }
    const horizon = { component_class: 'Settings', name: 'Solver settings', field: 'rolling_horizon',
      label: 'Horizon', old: 168, new: 24, unit: 'snapshots', role: 'typed', old_applies: false }
    const note = { component_class: '', name: '', field: 'solve_strategy', label: 'solve_strategy', old: null,
      new: null, unit: '', role: 'note', note: 'Rolling dispatch solves the horizon in windows.' }
    useChatStore.setState({ pending: { ...pending, edit_card: { ...card, rows: [strategy, horizon, note] } } })
    render(<ConfirmationCard />)
    const [s, h] = screen.getAllByTestId('chat-edit-row-typed').map(r => within(r).getAllByRole('cell').map(c => c.textContent))
    expect(s.slice(2, 4)).toEqual([oldWord, newWord])
    expect(h.slice(2, 5)).toEqual(['\u2014', '24', 'snapshots'])
    expect(screen.queryByTestId('chat-edit-row-note')).toBeNull()
    expect(screen.getByTestId('chat-edit-card-note').textContent).toBe('Rolling dispatch solves the horizon in windows.')
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

it('a row without a display value rounds a long number but keeps years, tiny values and no-limit', () => {
  const pending = useChatStore.getState().pending!
  const row = (field: string, old: number, nw: number) => ({ ...typed, field, old, new: nw, unit: 'x' })
  const rows = [row('a', 0.1234567891, 123456789012.3456), row('b', 2030, 2035), row('c', 1e-5, Infinity)]
  useChatStore.setState({ pending: { ...pending, edit_card: { ...card, rows } } })
  render(<ConfirmationCard />)
  const [long, year, tiny] = screen.getAllByTestId('chat-edit-row-typed').map(r => r.textContent ?? '')
  expect(long).toContain('0.123457')
  expect(long).toContain('123456789012')
  expect(year).toContain('2030')
  expect(year).not.toContain('2,030')
  expect(tiny).toContain('0.00001')
  expect(tiny).toContain('no limit')
})

it('the project change preview says "not set" and, in Guided, the field in words (LC-16 UX)', () => {
  const preview = { preview_id: 'p', valid: true, changes_total: 1, diff: [{ component_class: 'StorageUnit',
    name: 'Battery', attribute: 'inv_power_overnight', label: 'Power part: overnight cost',
    before: 'not set', after: 150, unit: 'EUR/kW' }] }
  for (const [mode, field] of [['expert', 'inv_power_overnight'], ['guided', 'Power part: overnight cost']]) {
    useUIStore.setState({ uiMode: mode as 'expert' | 'guided' })
    render(<AssistantResultCard result={preview} disabled={false} onRequest={vi.fn()} />)
    const cells = within(screen.getByRole('table', { name: 'Project change preview' })).getAllByRole('cell')
      .map(c => c.textContent)
    expect(cells).toEqual(['Battery', field, 'not set', '150', 'EUR/kW'])
    expect(screen.queryByText(/nan/)).toBeNull()
    cleanup()
  }
})

// LC-16 OJ-22: a selector or scale change shows one set line; the header names the set, not its members.
const solar = (name: string, old: number) => ({ component_class: 'Generator', name, field: 'p_nom', label: 'Capacity',
  old, new: old * 1.2, unit: 'MW', role: 'typed' })
const solarSet = { component_class: 'Generator', attribute: 'p_nom', selector: { carrier: 'solar' },
  names: ['Solar', 'Solar roof', 'Solar old'], size: 3, inactive: 1, inactive_names: ['Solar old'],
  words: 'all Generator with carrier solar, as of this preview', factor: 1.2, factor_words: '× 1.2 (+20 %)' }
const setCard = { ...card, tool: 'apply_project_changes', says: 'all Generator with carrier solar, as of this '
  + 'preview · 3 assets · p_nom × 1.2 (+20 %)', rows: [solar('Solar', 80), solar('Solar roof', 10), solar('Solar old', 5)],
  sets: [solarSet] }

function showSetCard(edit: object) {
  const pending = useChatStore.getState().pending!
  useChatStore.setState({ pending: { ...pending, tool_name: 'apply_project_changes', args: { preview_id: 'p' },
    safety_tier: 'execution', edit_card: edit } })
  render(<ConfirmationCard />)
}

it.each([
  ['guided', 'Every Generator with carrier Solar · 3 assets, 1 inactive · Capacity × 1.2 (+20 %)'],
  ['expert', 'all Generator with carrier solar, as of this preview · 3 assets, 1 inactive · p_nom × 1.2 (+20 %)'],
] as const)('a 3-asset set shows one set line and every row in %s mode', (uiMode, line) => {
  useUIStore.setState({ uiMode })
  showSetCard(setCard)
  expect(screen.getByTestId('chat-edit-set').textContent).toBe(line)
  expect(screen.getByTestId('chat-edit-header').textContent).toBe('Every Generator with carrier Solar')
  expect(screen.getAllByTestId('chat-edit-row-typed')).toHaveLength(3)
  if (uiMode === 'guided') expect(screen.getByTestId('chat-confirmation-summary').textContent).toBe(line)
})

it('the inactive member is marked in the table', () => {
  useUIStore.setState({ uiMode: 'guided' })
  showSetCard(setCard)
  const assets = screen.getAllByTestId('chat-edit-row-typed').map(r => r.querySelector('td')!.textContent)
  expect(assets).toEqual(["Generator 'Solar'", "Generator 'Solar roof'", "Generator 'Solar old' (inactive)"])
})

it.each([
  ['guided', 'Every Generator with carrier Solar · 3 assets, 1 inactive · Capacity × 1.2 (+20 %). Read back from the project.'],
  ['expert', 'all Generator with carrier solar, as of this preview · 3 assets · p_nom × 1.2 (+20 %). Read back from the project.'],
] as const)('the %s receipt words the set as the card did', (uiMode, line) => {
  useUIStore.setState({ uiMode })
  const receipt = { version: 1, digest: 'd', outcome: 'verified', rows: setCard.rows, says: setCard.says,
    results_stale: false, undo: { tool: 'undo_last', available: false }, sets: [solarSet] }
  render(<AssistantResultCard result={{ _edit_receipt: receipt }} disabled={false} onRequest={vi.fn()} />)
  expect(screen.getByTestId('chat-edit-receipt').textContent).toBe(line)
})

it('a 1-asset set says "1 asset"; a set given a value shows the new value; a bus set on a Link says any port', () => {
  useUIStore.setState({ uiMode: 'guided' })
  showSetCard({ ...setCard, rows: [{ component_class: 'Link', name: 'HP', field: 'p_nom', label: 'Capacity',
    old: 4, new: 5, unit: 'MW', role: 'typed' }], sets: [{ ...solarSet, component_class: 'Link',
    selector: { bus: 'B2' }, names: ['HP'], size: 1, inactive: 0, factor: null, factor_words: null }] })
  expect(screen.getByTestId('chat-edit-set').textContent)
    .toBe('Every Link at bus B2 (any port) · 1 asset · Capacity → 5 MW')
})

it('a names-form scale lists its members in the set line', () => {
  useUIStore.setState({ uiMode: 'guided' })
  showSetCard({ ...setCard, rows: [solar('Solar', 80)], sets: [{ ...solarSet, selector: null, names: ['Solar'],
    size: 1, inactive: 0 }] })
  expect(screen.getByTestId('chat-edit-set').textContent).toBe("Generator 'Solar' · 1 asset · Capacity × 1.2 (+20 %)")
})

it('a malformed set line cannot be approved', () => {
  showSetCard({ ...setCard, sets: [{ ...solarSet, names: 'Solar' }] })
  expect((screen.getByRole('button', { name: 'Approve' }) as HTMLButtonElement).disabled).toBe(true)
})

it('the Guided preview result shows the set line above its rows', () => {
  useUIStore.setState({ uiMode: 'guided' })
  const preview = { preview_id: 'p', valid: true, changes_total: 1, sets: [{ ...solarSet, names: ['Solar'], size: 1,
    inactive: 0 }], diff: [{ component_class: 'Generator', name: 'Solar', attribute: 'p_nom', label: 'Capacity',
    before: 80, after: 96, unit: 'MW' }] }
  render(<AssistantResultCard result={preview} disabled={false} onRequest={vi.fn()} />)
  expect(screen.getByTestId('assistant-change-set').textContent)
    .toBe('Every Generator with carrier Solar · 1 asset · Capacity × 1.2 (+20 %)')
})

it('a bus set on a Line says any port, as the server matches bus0 or bus1', () => {
  useUIStore.setState({ uiMode: 'guided' })
  showSetCard({ ...setCard, rows: [{ component_class: 'Line', name: 'L1', field: 's_nom', label: 'Rating',
    old: 100, new: 120, unit: 'MVA', role: 'typed' }], sets: [{ ...solarSet, component_class: 'Line',
    attribute: 's_nom', selector: { bus: 'B2' }, names: ['L1'], size: 1, inactive: 0 }] })
  expect(screen.getByTestId('chat-edit-header').textContent).toBe('Every Line at bus B2 (any port)')
})
