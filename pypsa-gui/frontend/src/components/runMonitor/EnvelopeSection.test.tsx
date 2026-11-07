// Study harness issue 08: the plan-review card's Envelope section (UX
// conditions 2 and 3; design §7 "Grant").
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ChoiceCard } from '../ChatPanel'
import { useChatStore } from '../../store/chatStore'
import { useUIStore } from '../../store/uiStore'
import * as api from '../../api/runMonitor'
import { ENVELOPE, STATES, TASK_ID } from './runFixtures'

vi.mock('../../api/runMonitor', () => ({
  getRun: vi.fn(), presentRun: vi.fn(), pauseRun: vi.fn(), resumeRun: vi.fn(), stopRun: vi.fn(),
}))

const REVIEW = {
  tool_use_id: 'tu_9', intent: 'plan_review' as const, title: 'Own site study', question: 'Approve this plan?',
  detail: '1. Brief\n2. Set up', options: [{ label: 'Approve' }, { label: 'Change' }], allow_free_text: true,
}
const STARTED = { id: 'm8', ts: 0, role: 'tool' as const, content: '', tool_use_id: 'tu_8', tool_name: 'start_task',
  tool_result: { task_id: TASK_ID, title: 'Own site study', status: 'ready', run: { stage: 'intake' }, envelope: ENVELOPE } }

function open(monitor: Partial<typeof STATES.proposal.monitor> = {}) {
  vi.mocked(api.getRun).mockResolvedValue({ ...STATES.proposal, monitor: { ...STATES.proposal.monitor, ...monitor } })
  useChatStore.setState({ choice: REVIEW, messages: [STARTED] })
  render(<ChoiceCard />)
}

beforeEach(() => {
  cleanup()
  vi.clearAllMocks()
  useChatStore.setState({ choice: null, messages: [], requestQueue: [], lastRequest: null, streaming: false })
})

describe('EnvelopeSection', () => {
  it('names every edit, run and budget in words with units, never a raw key', async () => {
    open()
    await waitFor(() => expect(screen.getByTestId('envelope-budgets').textContent).toContain('Cost cap €5.00'))
    expect(screen.getAllByTestId('envelope-edit').map(e => e.textContent)).toEqual([
      "New gensets · Maximum capacity: 0 MW or more, within ±50 % of today's value",
      "New PV · Maximum capacity: 0 MW or more, within ±50 % of today's value",
      "New batteries · Maximum capacity: 0 MW or more, within ±50 % of today's value",
      'New batteries · Storage hours: 1 h to 8 h',
    ])
    expect(screen.getByTestId('envelope-run').textContent).toBe('Queued solves')
    expect(screen.getByTestId('envelope-research').textContent).toBe('No research')
    const text = screen.getByTestId('envelope-section').textContent ?? ''
    expect(text).not.toMatch(/p_nom|max_hours|Generator|StorageUnit|solve_queue|update_component|\{|\}/)
  })

  it('has exactly three verdicts and no other choice', async () => {
    open()
    await screen.findByTestId('envelope-section')
    expect(screen.getAllByTestId('envelope-verdict').map(b => b.textContent?.split('Every')[0])).toEqual(
      ['Approve and run unattended', 'Approve, step by step', 'Change'])
    expect(screen.queryAllByTestId('chat-choice-option')).toHaveLength(0)
    expect(screen.queryByTestId('chat-choice-free-text')).toBeNull()
  })

  it.each([
    ['runner_off', 'Unattended runs are off on this server'],
    ['no_provider', 'Needs a model key: chat is off'],
    ['locked', 'Another user is editing this project'],
  ] as const)('disables unattended with its reason in visible text (%s)', async (reason, words) => {
    open({ unattended: { available: false, reason } })
    expect((await screen.findByTestId('envelope-unattended-reason')).textContent).toBe(words)
    expect((screen.getAllByTestId('envelope-verdict')[0] as HTMLButtonElement).disabled).toBe(true)
  })

  it('grants the narrowed envelope as the harness turn, never wider', async () => {
    open({ email: { available: false } })
    await waitFor(() => expect((screen.getAllByTestId('envelope-verdict')[0] as HTMLButtonElement).disabled).toBe(false))
    expect(screen.getByTestId('envelope-email-reason').textContent).toBe(': email is not set up on this server')
    // The reason runs on from the words in one inline run, so no flex gap opens before its colon.
    expect(screen.getByTestId('envelope-email-reason').parentElement?.textContent)
      .toBe('Email me when it needs me or ends: email is not set up on this server')
    expect(screen.getByTestId('envelope-email-reason').parentElement).not.toBe(screen.getByTestId('envelope-email'))
    expect((screen.getByTestId('envelope-email').querySelector('input') as HTMLInputElement).disabled).toBe(true)
    fireEvent.click(screen.getAllByTestId('envelope-edit')[1].querySelector('input')!)
    fireEvent.change(screen.getByTestId('envelope-solves'), { target: { value: '4' } })
    fireEvent.change(screen.getByTestId('envelope-hours'), { target: { value: '99' } })  // never above the proposal
    fireEvent.click(screen.getAllByTestId('envelope-verdict')[0])
    const [request] = useChatStore.getState().requestQueue
    const decision = request.harness!.envelope_decision!
    expect(decision.task_id).toBe(TASK_ID)
    expect(decision.verdict).toBe('unattended')
    expect(decision.notify_email).toBe(false)
    const envelope = decision.envelope as { edits: unknown[]; budget: unknown }
    expect(envelope.edits).toHaveLength(3)
    expect(envelope.budget).toEqual({ solves: 4, rounds: 6, wall_time_h: 8 })
    expect(request.label).toBe('Own site study: Approve and run unattended')
    expect(useChatStore.getState().choice).toBeNull()
  })

  it('the budget controls join with · only between items: each row starts and ends with an item', async () => {
    open()
    await waitFor(() => expect(screen.getByTestId('envelope-budgets').textContent).toContain('Cost cap €5.00'))
    const rows = screen.getAllByTestId('envelope-budget-row').map(r => r.textContent ?? '')
    // (flex items: textContent has no spaces between them)
    expect(rows).toEqual(['Solves up to·Rounds up to 6', 'Expires afterh·Cost cap €5.00'])
  })

  it('an unticked edit stays visible, marked removed; ticking it again restores it', async () => {
    open()
    await screen.findByTestId('envelope-section')
    const box = screen.getAllByTestId('envelope-edit')[1].querySelector('input')!
    fireEvent.click(box)
    expect(screen.getAllByTestId('envelope-edit')).toHaveLength(4)
    expect(screen.getAllByTestId('envelope-edit')[1].textContent).toMatch(/^New PV · Maximum capacity: .* · removed$/)
    expect(screen.getAllByTestId('envelope-removed')).toHaveLength(1)
    // The strikethrough stops at the edit: its "· removed" is a box of its own, never struck.
    expect(screen.getByTestId('envelope-removed').closest('.line-through')).toBeNull()
    expect(screen.getByTestId('envelope-removed').className).toMatch(/\binline-block\b/)
    fireEvent.click(box)
    expect(screen.queryByTestId('envelope-removed')).toBeNull()
  })

  it('the steppers have accessible names; an unset cost cap is said in words', async () => {
    open({ budgets: { cost_eur: { used: 0, limit: 0, unmetered: false } } })
    expect(await screen.findByRole('spinbutton', { name: 'Solves, at most' })).toBeTruthy()
    expect(screen.getByRole('spinbutton', { name: 'Hours until it expires, at most' })).toBeTruthy()
    await waitFor(() => expect(screen.getByTestId('envelope-budgets').textContent).toContain('no cost cap'))
  })

  it('step by step grants nothing; Change is an ordinary answer', async () => {
    open()
    fireEvent.click((await screen.findAllByTestId('envelope-verdict'))[1])
    expect(useChatStore.getState().requestQueue[0].harness).toEqual(
      { envelope_decision: { task_id: TASK_ID, verdict: 'stepwise' } })
    useChatStore.setState({ choice: REVIEW, requestQueue: [], lastRequest: null })
    fireEvent.click((await screen.findAllByTestId('envelope-verdict'))[2])
    const [change] = useChatStore.getState().requestQueue
    expect(change.text).toBe('Change')
    expect(change.harness).toBeUndefined()
  })

  it('a plan review with no run keeps its own options', () => {
    useChatStore.setState({ choice: REVIEW, messages: [] })
    render(<ChoiceCard />)
    expect(screen.queryByTestId('envelope-section')).toBeNull()
    expect(screen.getAllByTestId('chat-choice-option')).toHaveLength(2)
  })
})

// SH-26: the plan card names the Library tariffs held now, under the attach_tariff gap.
describe('EnvelopeSection held tariffs', () => {
  const GAP = { tool: 'attach_tariff', target: { component: 'Settings', name: 'Commercial', attr: 'import_tariff_ref' },
    source: { kind: 'library_tariff' } }
  const H1 = 'a1b2c3d4e5f6'.padEnd(64, '0'), H2 = '99887766aabb'.padEnd(64, '1')
  function openWith(gaps: unknown) {
    const envelope = { ...ENVELOPE, ...(gaps === undefined ? {} : { gaps }) }
    vi.mocked(api.getRun).mockResolvedValue({ ...STATES.proposal, envelope } as never)
    useChatStore.setState({ choice: REVIEW, messages: [{ ...STARTED, tool_result: { ...STARTED.tool_result, envelope } }] })
    render(<ChoiceCard />)
  }

  const TWO = [{ name: 'nl', version: 1, hash: H1 }, { name: 'other', version: 3, hash: H2 }]

  it('lists two held tariffs and says approving fills none; Guided shows no hash', async () => {
    useUIStore.setState({ uiMode: 'guided' })
    openWith([{ ...GAP, held_state: 'held', held: TWO }])
    await screen.findByTestId('envelope-held')
    expect(screen.getAllByTestId('envelope-held-tariff').map(e => e.textContent)).toEqual(['nl v1', 'other v3'])
    const text = screen.getByTestId('envelope-held').textContent ?? ''
    expect(text).toContain('Held now (read again when you approve):')
    expect(text).toContain('2 are held now, so approving fills none: keep one in the Library to fill it.')
    expect(text).not.toMatch(/grant|gap|import_tariff_ref|a1b2c3d4/)
  })

  it('Expert adds the short hash', async () => {
    useUIStore.setState({ uiMode: 'expert' })
    openWith([{ ...GAP, held_state: 'held', held: TWO }])
    await screen.findByTestId('envelope-held')
    expect(screen.getAllByTestId('envelope-held-tariff').map(e => e.textContent)).toEqual(['nl v1 · a1b2c3d4', 'other v3 · 99887766'])
  })

  it('one held says approving fills it if it is still the only one', async () => {
    openWith([{ ...GAP, held_state: 'held', held: TWO.slice(0, 1) }])
    expect((await screen.findByTestId('envelope-held-note')).textContent)
      .toBe('Approving fills it with this one if it is still the only one.')
  })

  it('never shows a raw attribute key for another tariff attribute', async () => {
    openWith([{ ...GAP, target: { ...GAP.target, attr: 'export_tariff_ref' }, held_state: 'none', held: [] }])
    expect((await screen.findByTestId('envelope-held')).textContent).not.toMatch(/_ref|export_tariff/)
  })

  it.each([
    ['none', [], 'No Library tariff is held, so approving leaves it empty.'],
    ['unreadable', null, 'Library tariffs could not be read now: they are read again when you approve.'],
    ['unnamed', null, 'A Library tariff without an id is held, so approving fills none.'],
  ])('says so when the state is %s', async (state, held, words) => {
    openWith([{ ...GAP, held_state: state, held }])
    expect((await screen.findByTestId('envelope-held-note')).textContent).toBe(words)
    expect(screen.queryAllByTestId('envelope-held-tariff')).toHaveLength(0)
  })

  it('renders no line for an envelope without them, as before', async () => {
    openWith(undefined)
    await screen.findByTestId('envelope-section')
    expect(screen.queryByTestId('envelope-held')).toBeNull()
  })
})

describe('EnvelopeSection reliability goal', () => {
  // GK B (33) P5a, GK B (34) F1: the card shows the goal with unit and source and echoes it at the grant.
  function openWith(goal: api.EnvelopeGoal | undefined) {
    const envelope = { ...ENVELOPE, ...(goal ? { goal } : {}) }
    vi.mocked(api.getRun).mockResolvedValue({ ...STATES.proposal, envelope } as never)
    useChatStore.setState({ choice: REVIEW, messages: [{ ...STARTED, tool_result: { ...STARTED.tool_result, envelope } }] })
    render(<ChoiceCard />)
  }

  it('shows a template goal with its unit and source and sends it back with the grant', async () => {
    const goal = { value: 2, unit: 'h/yr' as const, source: 'template' }
    openWith(goal)
    await waitFor(() => expect((screen.getAllByTestId('envelope-verdict')[0] as HTMLButtonElement).disabled).toBe(false))
    expect(screen.getByTestId('envelope-goal').textContent).toBe('Reliability goal (LOLE): 2 h/yr, from the eh_site template')
    fireEvent.click(screen.getAllByTestId('envelope-verdict')[0])
    expect(useChatStore.getState().requestQueue[0].harness!.envelope_decision!.goal).toEqual(goal)
  })

  it('says when no goal is set, and a run without a goal row sends none', async () => {
    openWith({ value: null, unit: 'h/yr', source: 'default' })
    await waitFor(() => expect(screen.getByTestId('envelope-goal').textContent).toContain('No reliability goal is set'))
    cleanup()
    openWith(undefined)
    await waitFor(() => expect((screen.getAllByTestId('envelope-verdict')[0] as HTMLButtonElement).disabled).toBe(false))
    expect(screen.queryByTestId('envelope-goal')).toBeNull()
    fireEvent.click(screen.getAllByTestId('envelope-verdict')[0])
    expect(useChatStore.getState().requestQueue.at(-1)!.harness!.envelope_decision!.goal).toBeUndefined()
  })
})
