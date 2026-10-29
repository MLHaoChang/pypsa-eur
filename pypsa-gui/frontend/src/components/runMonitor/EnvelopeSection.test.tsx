// Study harness issue 08: the plan-review card's Envelope section (UX
// conditions 2 and 3; design §7 "Grant").
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ChoiceCard } from '../ChatPanel'
import { useChatStore } from '../../store/chatStore'
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

  it('an unticked edit stays visible, marked removed; ticking it again restores it', async () => {
    open()
    await screen.findByTestId('envelope-section')
    const box = screen.getAllByTestId('envelope-edit')[1].querySelector('input')!
    fireEvent.click(box)
    expect(screen.getAllByTestId('envelope-edit')).toHaveLength(4)
    expect(screen.getAllByTestId('envelope-edit')[1].textContent).toMatch(/^New PV · Maximum capacity: .* · removed$/)
    expect(screen.getAllByTestId('envelope-removed')).toHaveLength(1)
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
