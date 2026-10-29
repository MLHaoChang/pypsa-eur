// Study harness issue 08: the plan card for a harness run (UX conditions
// 1, 4, 6, 7, 8 and the issue's acceptance criteria).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import AssistantResultCard from '../AssistantResultCard'
import { useChatStore } from '../../store/chatStore'
import { useUIStore } from '../../store/uiStore'
import * as api from '../../api/runMonitor'
import { STATES, TASK_ID } from './runFixtures'
import { STAGE_WORDS } from './runWords'
import { runUnderReview } from './planReview'

vi.mock('../../api/runMonitor', () => ({
  getRun: vi.fn(), presentRun: vi.fn(), pauseRun: vi.fn(), resumeRun: vi.fn(), stopRun: vi.fn(),
}))

const START = { task_id: TASK_ID, title: 'Own site study', status: 'ready', run: { stage: 'inputs' } }

function show(state: keyof typeof STATES, mode: 'guided' | 'expert' = 'guided', onRequest = vi.fn()) {
  useUIStore.setState({ uiMode: mode })
  vi.mocked(api.getRun).mockResolvedValue(STATES[state])
  return render(<AssistantResultCard result={START} disabled={false} onRequest={onRequest} />)
}

/** No internal task, run or stage status, reason or id in the card's text. */
const INTERNAL = /cancelled|needs_user|needs_review|waiting_job|completed|paused_at|envelope_\w+|step_failed|input_gate|result_gate|handoff|\{/

const card = () => screen.getByTestId('run-monitor').textContent ?? ''

beforeEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.mocked(api.presentRun).mockResolvedValue()
  useChatStore.setState({ pending: null, streaming: false, requestQueue: [], lastRequest: null, sessionId: 's1' })
  useUIStore.setState({ activeSlidePanel: null, flowReturn: null, fieldDrawer: null })
})
afterEach(() => cleanup())

describe('RunMonitor', () => {
  it('names every stage in the user words, in run order, and never an internal id or status', async () => {
    show('gate_pass')
    await waitFor(() => expect(screen.getAllByTestId('run-stage')).toHaveLength(9))
    expect(screen.getAllByTestId('run-stage').map(s => s.textContent?.split(' · ')[0])).toEqual(
      ['Brief', 'Set up', 'Inputs', 'Check inputs', 'Solve', 'Check results', 'Explore options', 'Report', 'Hand-off'])
    expect(Object.values(STAGE_WORDS)).toHaveLength(9)
    expect(card()).not.toMatch(/input_gate|result_gate|handoff|needs_user|validate\.errors|\{/)
  })

  it('expands the current stage: gate rules pass or fail, loop round, budgets used with units', async () => {
    show('gate_pass')
    await screen.findByTestId('run-current-stage')
    expect(screen.getAllByTestId('run-gate-rule').map(r => r.textContent)).toEqual(
      ['The model has no errors · 0 errors · pass', 'The model is ready to solve · pass'])
    expect(screen.getByTestId('run-budgets').textContent).toMatch(/^Cost €0\.42 of €5\.00 · Solves 3 of 6 · Expires \d+ Oct \d\d:\d\d$/)
    cleanup()
    show('loop')
    expect((await screen.findByTestId('run-loop')).textContent).toBe('Round 2 of 6')
  })

  it('Guided and Expert show the same card; Expert only adds a closed Details', async () => {
    show('gate_fail', 'guided')
    await screen.findByTestId('run-failure')
    const guided = card()
    expect(screen.queryByText('Details')).toBeNull()
    cleanup()
    show('gate_fail', 'expert')
    await screen.findByTestId('run-failure')
    expect(card().startsWith(guided)).toBe(true)
    expect(screen.getByText('Details').closest('details')?.open).toBe(false)
  })

  it.each(Object.keys(STATES).filter(k => !k.startsWith('envelope_')))(
    'says the status in words, never an internal status; only Expert Details shows the ids (%s)', async (state) => {
      show(state, 'guided')
      await screen.findByTestId('run-state')
      expect(card()).not.toMatch(INTERNAL)
      expect(card()).not.toContain(TASK_ID)
      expect(screen.getByTestId('run-state').textContent).toMatch(/^[A-Z][a-z ]/)
      cleanup()
      show(state, 'expert')
      await screen.findByTestId('run-state')
      const details = screen.getByText('Details').closest('details')!.textContent ?? ''
      expect(details).toContain(`Task ${TASK_ID}`)
      expect(details).toMatch(/Stage [a-z_]+$/)
      expect(card().replace(details, '')).not.toMatch(INTERNAL)
      expect(details).not.toMatch(/cancelled|needs_user|needs_review|completed|running|paused|budget|step_failed/)
    })

  it.each([
    ['gate_fail', /^Check inputs failed: 1 rule did not pass · See the issues › then Check again$/],
    ['over_budget', /^Cost budget used up: the run stopped: Change the budget ›$/],
    ['solves_used_up', /^Solve budget used up: the run stopped: Change the budget ›$/],
    ['expired', /^Unattended approval expired: Continue step by stepNothing runs until you resume: it picks up at Solve\.$/],
    ['solver_error', /^Solve did not finish: Open the solve log ›$/],
    ['out_of_date', /^Inputs out of date: Review a new brief ›$/],
  ] as const)('says the %s failure in words with a › fix', async (state, words) => {
    show(state)
    expect((await screen.findByTestId('run-failure')).textContent).toMatch(words)
  })

  it('Expert: a › link opens its panel through openFlowTarget, with Back to the origin; never on its own', async () => {
    show('finished_report', 'expert')
    await screen.findByTestId('run-finished')
    expect(useUIStore.getState().activeSlidePanel).toBeNull()
    fireEvent.click(screen.getByTestId('run-report-link'))
    expect(useUIStore.getState().activeSlidePanel).toBe('reports')
    expect(useUIStore.getState().uiMode).toBe('expert')
    expect(useUIStore.getState().flowReturn).not.toBeNull()
  })

  it('Guided: a › link opens its panel in place over the cards (openFlowTargetInPlace), never flipping the mode', async () => {
    show('solver_error', 'guided')
    fireEvent.click(await screen.findByRole('button', { name: 'Open the solve log ›' }))
    const ui = useUIStore.getState()
    expect(ui.uiMode).toBe('guided')
    expect(ui.fieldDrawer).toMatchObject({ panel: 'solveQueue', section: 'readiness' })
    expect(ui.activeSlidePanel).toBeNull()
    expect(ui.flowReturn).not.toBeNull()
    cleanup()
    useUIStore.setState({ fieldDrawer: null, flowReturn: null })
    show('gate_fail', 'expert')
    fireEvent.click(await screen.findByRole('button', { name: 'See the issues ›' }))
    expect(useUIStore.getState().activeSlidePanel).toBe('issues')
  })

  it('solve budget used up: "Change the budget ›" reopens this run\u2019s Envelope section, not Resume', async () => {
    useChatStore.setState({ messages: [], choice: null })
    show('solves_used_up')
    fireEvent.click(await screen.findByRole('button', { name: 'Change the budget ›' }))
    expect(useChatStore.getState().choice).toMatchObject({ intent: 'plan_review', title: 'Own site study' })
    expect(runUnderReview(useChatStore.getState().messages)?.taskId).toBe(TASK_ID)
    expect(useChatStore.getState().requestQueue).toHaveLength(0)
    expect(api.resumeRun).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: /Resume/ })).toBeNull()
  })

  it.each([
    ['over_budget', 'Change the budget ›', 'Change the budget'],
    ['uncertain', 'Review the step ›', 'Review the step'],
  ] as const)('%s: the request the fix sends shows in words, never a tool name or id', async (state, link, label) => {
    show(state)
    fireEvent.click(await screen.findByRole('button', { name: link }))
    const [request] = useChatStore.getState().requestQueue
    expect(request.label).toBe(label)
    expect(request.label).not.toMatch(/get_task|_task|[0-9a-f]{8}-/)
  })

  it('the Report row shows only once the run has a report', async () => {
    show('gate_pass')
    await screen.findByTestId('run-state')
    expect(screen.queryByTestId('run-report-link')).toBeNull()
    cleanup()
    show('finished')
    await screen.findByTestId('run-finished')
    expect(screen.queryByTestId('run-report-link')).toBeNull()
    cleanup()
    show('finished_report')
    expect((await screen.findByTestId('run-report-link')).textContent).toBe('Open the report ›')
  })

  it('links use ›, never >', async () => {
    for (const state of Object.keys(STATES).filter(k => !k.startsWith('envelope_'))) {
      show(state)
      await screen.findByTestId('run-state')
      expect(card()).not.toContain('>')
      // › only on a link that goes somewhere; an action in place has none.
      for (const button of screen.getByTestId('run-monitor').querySelectorAll('button')) {
        const marked = button.textContent?.endsWith(' ›') ?? false
        expect(marked, button.textContent ?? '').toBe(button.hasAttribute('data-run-link'))
        if (/^(Resume|Check again|Try again|Continue step by step)/.test(button.textContent ?? '')) expect(marked).toBe(false)
      }
      cleanup()
    }
  })

  it('paused: names the stage and how to resume; Resume goes on', async () => {
    vi.mocked(api.resumeRun).mockResolvedValue(STATES.gate_pass)
    show('paused')
    const failure = await screen.findByTestId('run-failure')
    expect(failure.textContent).toMatch(/^Paused at Solve: ResumeNothing runs until you resume: it picks up at Solve\.Unattended approval expires/)
    expect(screen.getByTestId('run-paused-expiry').textContent).toMatch(/^Unattended approval expires \d{1,2} Oct \d\d:\d\d$/)
    fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
    await waitFor(() => expect(api.resumeRun).toHaveBeenCalledWith(TASK_ID, 's1'))
  })

  it('Stop is an inline second step: Keep running calls nothing', async () => {
    show('stop_confirm')
    fireEvent.click(await screen.findByTestId('run-stop'))
    const step = screen.getByTestId('run-stop-confirm')
    expect(step.textContent).toBe('Stop the run?Stop·Keep runningThe edits applied so far and the results so far are kept. A stopped run cannot be restarted.')
    expect(screen.queryByTestId('run-stop')).toBeNull()
    fireEvent.click(screen.getByTestId('run-stop-keep'))
    expect(screen.queryByTestId('run-stop-confirm')).toBeNull()
    expect(api.stopRun).not.toHaveBeenCalled()
  })

  it('Stop says what it keeps; a stopped card names the stage, never an internal status', async () => {
    vi.mocked(api.stopRun).mockResolvedValue(STATES.stopped)
    show('gate_pass')
    fireEvent.click(await screen.findByTestId('run-stop'))
    expect(api.stopRun).not.toHaveBeenCalled()
    expect(screen.getByTestId('run-stop-note').textContent)
      .toBe('The edits applied so far and the results so far are kept. A stopped run cannot be restarted.')
    fireEvent.click(screen.getByTestId('run-stop-yes'))
    await waitFor(() => expect(api.stopRun).toHaveBeenCalledWith(TASK_ID))
    const stopped = await screen.findByTestId('run-stopped')
    expect(stopped.textContent).toMatch(/^Stopped at Solve: the edits applied so far and the results so far are kept\./)
    expect(card()).not.toMatch(/cancelled/i)
    expect(screen.queryByTestId('run-pause')).toBeNull()
  })

  it('Pause, Resume and Stop show only when the view lists them in monitor.actions', async () => {
    const paused = STATES.paused
    vi.mocked(api.getRun).mockResolvedValue({ ...paused, monitor: { ...paused.monitor, actions: ['stop'] } })
    useUIStore.setState({ uiMode: 'guided' })
    render(<AssistantResultCard result={START} disabled={false} onRequest={vi.fn()} />)
    await screen.findByTestId('run-failure')
    expect(screen.queryByRole('button', { name: 'Resume' })).toBeNull()
    expect(screen.queryByTestId('run-pause')).toBeNull()
    expect(screen.getByTestId('run-stop')).toBeTruthy()
    cleanup()
    vi.mocked(api.getRun).mockResolvedValue({ ...STATES.gate_pass, monitor: { ...STATES.gate_pass.monitor, actions: [] } })
    render(<AssistantResultCard result={START} disabled={false} onRequest={vi.fn()} />)
    await screen.findByTestId('run-state')
    expect(screen.queryByTestId('run-pause')).toBeNull()
    expect(screen.queryByTestId('run-stop')).toBeNull()
  })

  it('Pause asks the server and shows the paused card', async () => {
    vi.mocked(api.pauseRun).mockResolvedValue(STATES.paused)
    show('gate_pass')
    fireEvent.click(await screen.findByTestId('run-pause'))
    await waitFor(() => expect(api.pauseRun).toHaveBeenCalledWith(TASK_ID))
    expect((await screen.findByTestId('run-failure')).textContent).toMatch(/^Paused at Solve/)
  })

  it('a waiting card is issued when the plan card is open, and answered on the run session as the Confirmation card', async () => {
    const { card: issued, ...monitor } = STATES.waiting.monitor
    vi.mocked(api.getRun).mockResolvedValueOnce({ ...STATES.waiting, monitor })
      .mockResolvedValue(STATES.waiting)
    useUIStore.setState({ uiMode: 'guided' })
    render(<AssistantResultCard result={START} disabled={false} onRequest={vi.fn()} />)
    await waitFor(() => expect(api.presentRun).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(useChatStore.getState().pending?.confirmation_token).toBe(issued!.confirmation_token))
    expect(useChatStore.getState().pending?.session_id).toBe(`run:${TASK_ID}`)
    expect(screen.getByTestId('run-waiting-card').textContent).toBe('A step needs your approval: see the card below')
  })

  it('Continue to the next stage is the user\u2019s COP-1 click, sent as the harness turn', async () => {
    show('proposal')
    fireEvent.click(await screen.findByTestId('run-continue'))
    const [request] = useChatStore.getState().requestQueue
    expect(request.harness).toEqual({ continue_task_id: TASK_ID })
    expect(request.label).toBe('Continue to Inputs')
  })

  it('a run the caller can no longer reach says so in words (SC-16)', async () => {
    useUIStore.setState({ uiMode: 'guided' })
    vi.mocked(api.getRun).mockRejectedValue(Object.assign(new Error('404'), {
      isAxiosError: true, response: { status: 404, data: { detail: { error_kind: 'task_not_found' } } } }))
    render(<AssistantResultCard result={START} disabled={false} onRequest={vi.fn()} />)
    expect((await screen.findByTestId('run-gone')).textContent).toBe('This run is no longer available to you.')
  })
})
