import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ConfirmationCard } from './ConfirmationCard'
import { applyUiNavigate } from './uiNavigate'
import AssistantResultCard from './AssistantResultCard'
import { SWEEP_ROW_CAP } from './SweepCard'
import { useChatStore } from '../store/chatStore'
import { useUIStore } from '../store/uiStore'
import { postChatConfirm } from '../api/chat'

vi.mock('../api/chat', () => ({ postChatConfirm: vi.fn().mockResolvedValue({ ok: true }) }))

// OJ-27: one case, three rows, one solve (harness/sweep_card.py).
const card = {
  version: 1, kind: 'sweep', tool: 'run_sensitivity_sweep', baseline: { project_id: 'p-base', name: 'Campus' },
  solves: 1, digest: 'd'.repeat(64), says: '1 sensitivity case from Campus: battery-capex-70pct-lines-500; queues 1 solve',
  cases: [{ name: 'battery-capex-70pct-lines-500', derived: true, existing: false, rows: [
    { component_class: 'Store', name: 'Battery', field: 'capital_cost', label: 'Capital cost', base: 100000, case: 70000,
      unit: 'EUR/MW', display: { base: 100, case: 70, unit: 'EUR/kW' } },
    { component_class: 'Line', name: 'L1', field: 's_nom', label: 'Rating', base: 300, case: 500, unit: 'MVA' },
    { component_class: 'Line', name: 'L2', field: 's_nom', label: 'Rating', base: 300, case: 500, unit: 'MVA' },
  ] }],
}

beforeEach(() => {
  vi.clearAllMocks()
  useUIStore.setState({ assistantDockOpen: true, uiMode: 'expert', projectMismatch: null })
  useChatStore.setState({ sessionId: 'session-1', pending: {
    tool_use_id: 'call-1', tool_name: 'run_sensitivity_sweep', args: { baseline_project_id: 'p-base', cases: [] },
    safety_tier: 'execution', confirmation_token: 'token-1', ttl_seconds: 300,
    expires_at_epoch_ms: Date.now() + 300_000, sweep_card: card,
  } })
})
afterEach(cleanup)

it('shows each case by name with every base → case row and the solves it queues', async () => {
  render(<ConfirmationCard />)
  const table = screen.getByRole('table', { name: 'battery-capex-70pct-lines-500' })
  const rows = within(table).getAllByRole('row')
  expect(rows).toHaveLength(4)
  expect(within(table).getAllByRole('columnheader').map(c => c.textContent)).toEqual(['Asset', 'Field', 'Base', 'Case', 'Unit'])
  const battery = within(rows[1]).getAllByRole('cell').map(c => c.textContent)
  expect(battery.slice(0, 4)).toEqual(["Store 'Battery'", 'capital_cost', '100', '70'])
  expect(battery[4]).toContain('EUR/kW')
  expect(within(rows[3]).getAllByRole('cell').map(c => c.textContent)).toEqual(["Line 'L2'", 's_nom', '300', '500', 'MVA'])
  expect(screen.getByTestId('chat-sweep-solves').textContent).toContain('Queues 1 solve.')
  await userEvent.click(screen.getByRole('button', { name: 'Approve' }))
  expect(postChatConfirm).toHaveBeenCalledWith('session-1', { token: 'token-1', decision: 'approve' })
})

it('a malformed sweep card cannot be approved', () => {
  const pending = useChatStore.getState().pending!
  useChatStore.setState({ pending: { ...pending, sweep_card: { ...card, cases: [] } } })
  render(<ConfirmationCard />)
  expect(screen.getByRole('alert').textContent).toContain('cases of this sweep are unavailable')
  expect((screen.getByRole('button', { name: 'Approve' }) as HTMLButtonElement).disabled).toBe(true)
})

it('opening Scenarios highlights the new cases until the next navigation', () => {
  applyUiNavigate({ kind: 'navigate', panel_id: 'scenarios', highlight_projects: ['p-a', 'p-b'] })
  expect(useUIStore.getState().activeSlidePanel).toBe('scenarios')
  expect(useUIStore.getState().scenariosHighlight).toEqual(['p-a', 'p-b'])
  applyUiNavigate({ kind: 'navigate', panel_id: 'results' })
  expect(useUIStore.getState().scenariosHighlight).toBeNull()
})

it('Guided shows the panel words; a long sweep says how many more cases change', () => {
  useUIStore.setState({ uiMode: 'guided' })
  const many = Array.from({ length: SWEEP_ROW_CAP }, (_, i) => ({ ...card.cases[0].rows[1], name: `L${i}` }))
  const cases = ['a', 'b', 'c'].map(name => ({ ...card.cases[0], name, rows: many }))
  const pending = useChatStore.getState().pending!
  useChatStore.setState({ pending: { ...pending, sweep_card: { ...card, cases, solves: 3 } } })
  render(<ConfirmationCard />)
  expect(screen.getAllByTestId('chat-sweep-case')).toHaveLength(1)
  expect(within(screen.getByRole('table', { name: 'a' })).getAllByRole('row')[1].textContent).toContain('Rating')
  expect(screen.getByTestId('chat-sweep-more-cases').textContent).toBe('2 more cases change.')
  expect(screen.getByTestId('chat-sweep-solves').textContent).toContain('Queues 3 solves.')
})

it("the sweep's result opens its cases in Scenarios only on a click, with a way back", async () => {
  useUIStore.setState({ activeSlidePanel: 'results', scenariosHighlight: null })
  render(<AssistantResultCard disabled={false} onRequest={vi.fn()} result={{ baseline_project_id: 'p-base',
    status: 'submitted', cases: [{ name: 'a', project_id: 'p-a' }, { name: 'b', project_id: 'p-b' }] }} />)
  expect(useUIStore.getState().activeSlidePanel).toBe('results')
  await userEvent.click(screen.getByRole('button', { name: 'Open the new cases in Scenarios ›' }))
  expect(useUIStore.getState().activeSlidePanel).toBe('scenarios')
  expect(useUIStore.getState().scenariosHighlight).toEqual(['p-a', 'p-b'])
})
