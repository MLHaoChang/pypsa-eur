// Study harness issue 08: the plan-review card's Envelope section (UX
// conditions 2 and 3; design §7 "Grant").
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ChoiceCard, KIND_COPY } from '../ChatPanel'
import { useChatStore } from '../../store/chatStore'
import { useUIStore } from '../../store/uiStore'
import * as api from '../../api/runMonitor'
import { RELIABILITY_GOAL_LABEL } from '../../hooks/useReliabilityGoal'
import realIntake from './intakeCard.fixture.json'
import { DEFAULT_WORDS, PATH_WORDS, setupWords } from './EnvelopeSection'
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
    expect(screen.getByTestId('envelope-goal').textContent).toBe("Reliability goal (LOLE): 2 h/yr, from the site template's default")
    fireEvent.click(screen.getAllByTestId('envelope-verdict')[0])
    expect(useChatStore.getState().requestQueue[0].harness!.envelope_decision!.goal).toEqual(goal)
  })

  it('names the missing goal, blocks only the unattended approval, and a run without a goal row sends none', async () => {
    openWith({ value: null, unit: 'h/yr', source: 'default' })
    await waitFor(() => expect(screen.getByTestId('envelope-unattended-reason').textContent)
      .toBe('Set the reliability goal first: see above.'))
    expect(screen.getByTestId('envelope-goal').textContent).toBe('No reliability goal is set. Missing: Reliability goal (LOLE) ›')
    const [unattended, stepwise] = screen.getAllByTestId('envelope-verdict') as HTMLButtonElement[]
    expect(unattended.disabled).toBe(true)
    expect(stepwise.disabled).toBe(false)
    cleanup()
    openWith(undefined)
    await waitFor(() => expect((screen.getAllByTestId('envelope-verdict')[0] as HTMLButtonElement).disabled).toBe(false))
    expect(screen.queryByTestId('envelope-goal')).toBeNull()
    fireEvent.click(screen.getAllByTestId('envelope-verdict')[0])
    expect(useChatStore.getState().requestQueue.at(-1)!.harness!.envelope_decision!.goal).toBeUndefined()
  })

  it.each(['guided', 'expert'] as const)('the missing goal opens its editor in %s, with Back to this card', async (mode) => {
    useUIStore.setState({ uiMode: mode, activeSection: 'project', activeSlidePanel: null, fieldDrawer: null, flowReturn: null })
    openWith({ value: null, unit: 'h/yr', source: 'default' })
    fireEvent.click(await screen.findByTestId('envelope-goal-fix'))
    const ui = useUIStore.getState()
    expect(ui.uiMode).toBe(mode)
    expect(ui.activeSection).toBe('scenarios')
    expect(ui.solverBlockRequest).toBe('goal')
    expect(ui.flowReturn).not.toBeNull()
    expect(screen.getByTestId('envelope-section')).toBeTruthy()
  })

  it('names the goal in the words the run intake and the template ledgers use', () => {
    const backend = resolve(__dirname, '../../../../backend')
    const profiles = readFileSync(resolve(backend, 'services/run_profiles.py'), 'utf8')
    expect(profiles).toContain(`_num(\n        "${RELIABILITY_GOAL_LABEL}"`)
    for (const t of ['eh_microgrid', 'eh_datacenter', 'eh_datacenter_staged']) {
      const ledger = JSON.parse(readFileSync(resolve(backend, `project_templates/${t}/eh_template.json`), 'utf8')).ledger
      expect(ledger.find((r: { field: string }) => r.field === 'target_lole_h').label).toBe(RELIABILITY_GOAL_LABEL)
    }
  })

  // Issue 37: the intake rows and the digest a grant sends back.
  const DIGEST = 'a'.repeat(64)
  const SETUP = { tool: 'create_project_from_template', template: 'eh_site', name: 'Acme site', params_digest: DIGEST }
  const row = (i: number, over: Record<string, unknown> = {}) => ({ index: i, param: `p${i}`, label: `Input ${i}`, value: i, unit: 'MW',
    status: 'confirmed', source: `you wrote 'x${i}'`, from_document: false, mapped: false, ...over })
  function openIntake(rows: ReturnType<typeof row>[], defaults: unknown[] = []) {
    const view = { ...STATES.proposal, envelope: { ...ENVELOPE, setup: SETUP },
      run: { ...STATES.proposal.run, intake: { card: { rows, defaults }, digest: DIGEST, missing: [] } } }
    vi.mocked(api.getRun).mockResolvedValue(view as never)
    useChatStore.setState({ choice: REVIEW, messages: [{ ...STARTED, tool_result: { ...STARTED.tool_result, envelope: { ...ENVELOPE, setup: SETUP } } }] })
    render(<ChoiceCard />)
  }

  it('shows the setup row first, in words, with no digest in Guided or Expert', async () => {
    for (const mode of ['guided', 'expert'] as const) {
      cleanup(); useUIStore.setState({ uiMode: mode }); openIntake([row(0)])
      await screen.findByTestId('envelope-intake')
      const section = screen.getByTestId('envelope-section')
      expect(section.firstElementChild!.getAttribute('data-testid')).toBe('envelope-setup')
      expect(screen.getByTestId('envelope-setup').textContent).toBe('Creates project Acme site from the site template')
      expect(section.textContent).not.toContain(DIGEST.slice(0, 8))
    }
  })

  it('lists rows with source, document mark, struck and conflict words, and preset defaults', async () => {
    openIntake([row(0, { from_document: true, source: 'sheet.pdf, page 2' }), row(1, { status: 'struck' }),
      row(2, { param: 'c', status: 'conflict', source: "you wrote 'a'" }), row(3, { param: 'c', status: 'conflict', source: 'b.xlsx S!A1' })],
      [{ param: 'critical_share', value: 0.4, preset_default: true }])
    await screen.findByTestId('envelope-intake')
    const rows = screen.getAllByTestId('envelope-intake-row')
    expect(rows[0].textContent).toContain('Input 0: 0 MW')
    expect(rows[0].textContent).toContain('read from a document')
    expect(rows[0].textContent).toContain('sheet.pdf, page 2')
    expect(rows[1].textContent).toContain('Struck')
    expect(rows[2].textContent).toContain("Not used: conflicts with b.xlsx S!A1")
    expect(screen.getByTestId('envelope-intake-default').textContent).toBe('Critical load share: 40 % · preset default')
  })

  it('shows every row and default, none folded', async () => {
    openIntake([0, 1, 2, 3, 4, 5, 6].map(i => row(i)), [{ param: 'a_b', value: 1, preset_default: true }])
    await screen.findByTestId('envelope-intake')
    expect(screen.getAllByTestId('envelope-intake-row')).toHaveLength(7)
    expect(screen.getAllByTestId('envelope-intake-default')).toHaveLength(1)
    expect(screen.queryByText(/Show all/)).toBeNull()
  })

  it('missing inputs are listed and no approve can be sent', async () => {
    const view = { ...STATES.proposal, envelope: { ...ENVELOPE, setup: SETUP },
      run: { ...STATES.proposal.run, intake: { card: { rows: [row(0)], defaults: [] }, digest: DIGEST,
        missing: [{ param: 'connection_mw' }], request: 'Connection capacity (MW)' } } }
    vi.mocked(api.getRun).mockResolvedValue(view as never)
    useChatStore.setState({ choice: REVIEW, messages: [{ ...STARTED, tool_result: { ...STARTED.tool_result, envelope: { ...ENVELOPE, setup: SETUP } } }] })
    render(<ChoiceCard />)
    expect((await screen.findByTestId('envelope-intake-missing')).textContent).toContain('Connection capacity (MW)')
    const [unattended, stepwise] = screen.getAllByTestId('envelope-verdict') as HTMLButtonElement[]
    expect(unattended.disabled).toBe(true); expect(stepwise.disabled).toBe(true)
    expect(screen.getByTestId('envelope-stepwise-reason').textContent).toContain('missing')
  })

  it('both approvals send the digest from the intake; step by step says it creates the project', async () => {
    openIntake([row(0)])
    await screen.findByTestId('envelope-intake')
    const buttons = await screen.findAllByTestId('envelope-verdict')
    expect(buttons[1].textContent).toContain('Creates project Acme site now; every other change and run asks you first.')
    await waitFor(() => expect((buttons[0] as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(buttons[0])
    expect(useChatStore.getState().requestQueue[0].harness!.envelope_decision!.intake_digest).toBe(DIGEST)
    useChatStore.setState({ choice: REVIEW, requestQueue: [], lastRequest: null })
    fireEvent.click((await screen.findAllByTestId('envelope-verdict'))[1])
    expect(useChatStore.getState().requestQueue[0].harness).toEqual(
      { envelope_decision: { task_id: TASK_ID, verdict: 'stepwise', intake_digest: DIGEST } })
  })

  it('a card without an intake keeps its rows and sends no digest', async () => {
    open()
    await screen.findByTestId('envelope-budgets')
    expect(screen.queryByTestId('envelope-setup')).toBeNull()
    expect(screen.queryByTestId('envelope-intake')).toBeNull()
    fireEvent.click((await screen.findAllByTestId('envelope-verdict'))[0])
    expect(useChatStore.getState().requestQueue[0].harness!.envelope_decision).not.toHaveProperty('intake_digest')
  })

  // GK B (46) F4: the return of `run_intake.card_view` + digest, dumped from the backend's own
  // test data (test_run_intake `_given`), not written by hand.
  it('renders the backend-shaped intake: every row and default, and echoes its digest', async () => {
    const view = { ...STATES.proposal, envelope: { ...ENVELOPE, setup: SETUP },
      run: { ...STATES.proposal.run, intake: realIntake } }
    vi.mocked(api.getRun).mockResolvedValue(view as never)
    useChatStore.setState({ choice: REVIEW, messages: [{ ...STARTED, tool_result: { ...STARTED.tool_result, envelope: { ...ENVELOPE, setup: SETUP } } }] })
    render(<ChoiceCard />)
    await screen.findByTestId('envelope-intake')
    expect(screen.getAllByTestId('envelope-intake-row')).toHaveLength(realIntake.card.rows.length)
    expect(screen.getAllByTestId('envelope-intake-default')).toHaveLength(realIntake.card.defaults.length)
    expect(screen.getAllByTestId('envelope-intake-row')[0].textContent).toContain("Grid connection: 12 MW")
    expect(screen.getAllByTestId('envelope-intake-row')[0].textContent).toContain("grid.pdf, page 1")
    const stepwise = (await screen.findAllByTestId('envelope-verdict'))[1]
    await waitFor(() => expect((stepwise as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(stepwise)
    expect(useChatStore.getState().requestQueue[0].harness!.envelope_decision!.intake_digest).toBe(realIntake.digest)
  })

  it('a preset default opens with its field label, never its key; an unknown template is not named by its id', async () => {
    for (const d of realIntake.card.defaults) expect(DEFAULT_WORDS[d.param], d.param).toBeTruthy()
    expect(setupWords('Acme', 'other_tpl')).toBe('Creates project Acme from the chosen template')
    expect(setupWords('Acme', 'eh_site')).toBe('Creates project Acme from the site template')
  })

  it("DEFAULT_WORDS agree with the backend profile's own field labels", () => {
    const src = readFileSync(resolve(__dirname, '../../../../backend/services/run_profiles.py'), 'utf8')
    for (const key of ['site_type', 'connection_mw', 'critical_share', 'load_profile', 'ride_through', 'fault_level_mva', 'horizon_end']) {
      const label = new RegExp(`"${key}": _(?:num|enum|bool)\\("([^"]+)"`).exec(src)?.[1]
      expect(DEFAULT_WORDS[key], key).toBe(label)
    }
  })

  it('a conflict row is warn-coloured, a struck row only muted', async () => {
    openIntake([row(0, { status: 'conflict', param: 'c' }), row(1, { status: 'struck' })])
    await screen.findByTestId('envelope-intake')
    expect(screen.getByTestId('envelope-intake-conflict').className).toContain('text-warn')
    expect(screen.getByTestId('envelope-intake-struck').className).toContain('text-muted')
  })

  it('shows every entry and nested value of a default, none as "N values" (GK B F2)', async () => {
    const view = { ...STATES.proposal, envelope: { ...ENVELOPE, setup: SETUP },
      run: { ...STATES.proposal.run, intake: realIntake } }
    vi.mocked(api.getRun).mockResolvedValue(view as never)
    useChatStore.setState({ choice: REVIEW, messages: [{ ...STARTED, tool_result: { ...STARTED.tool_result, envelope: { ...ENVELOPE, setup: SETUP } } }] })
    render(<ChoiceCard />)
    await screen.findByTestId('envelope-intake')
    const text = screen.getByTestId('envelope-intake').textContent!
    const stages = realIntake.card.defaults.find(d => d.param === 'stages')!.value as { year: number; peak_mw: number }[]
    for (const e of stages) expect(text).toContain(`${e.year}: ${e.peak_mw} MW peak`)
    const leaves: [string, unknown][] = []
    const walk = (path: string, v: unknown) => { if (v && typeof v === 'object') Object.entries(v).forEach(([k, x]) => walk(`${path}.${k}`, x)); else leaves.push([path, v]) }
    for (const d of realIntake.card.defaults) if (d.param === 'existing' || d.param === 'candidates') walk(d.param, d.value)
    expect(leaves.length).toBeGreaterThan(10)
    // F2a: every leaf shows its label, its value and its unit.
    for (const [path, v] of leaves) {
      const [label, unit] = PATH_WORDS[path]
      const shown = typeof v === 'boolean' ? (v ? 'Yes' : 'No') : unit ? `${v} ${unit}` : String(v)
      expect(text, path).toContain(`${label}: ${shown}`)
    }
    // F2b: every year-entry of both lists shows its year and its value.
    for (const [param, key, tail] of [['stages', 'peak_mw', 'MW peak'], ['energization', 'mw', 'MW energized']] as const) {
      const entries = realIntake.card.defaults.find(d => d.param === param)!.value as Record<string, number>[]
      expect(entries.length).toBeGreaterThan(0)
      for (const e of entries) expect(text, param).toContain(`${e.year}: ${e[key]} ${tail}`)
    }
    expect(text).not.toMatch(/\d+ (entries|values)/)
  })

  it("PATH_WORDS agree with the backend profile's field labels", () => {
    const src = readFileSync(resolve(__dirname, '../../../../backend/services/run_profiles.py'), 'utf8')
    for (const key of ['existing.gensets.count', 'existing.gensets.unit_mw', 'existing.gensets.fuel', 'existing.pv_mw', 'existing.battery.mw',
      'existing.battery.mwh', 'existing.ups.mw', 'existing.ups.minutes']) {
      const label = new RegExp(`"${key.replace(/\./g, '\\.')}": _(?:num|enum|bool)\\("([^"]+)"`).exec(src)?.[1]
      expect(PATH_WORDS[key][0], key).toBe(label)
    }
  })

  it('a refused intake digest has a plain-words title, never its kind key', () => {
    expect(KIND_COPY.intake_stale.title).toBe('The inputs changed: review them again')
  })
})
