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

vi.mock('../../api/runMonitor', () => ({
  getRun: vi.fn(), presentRun: vi.fn(), pauseRun: vi.fn(), resumeRun: vi.fn(), stopRun: vi.fn(),
}))

const START = { task_id: TASK_ID, title: 'Own site study', status: 'ready', run: { stage: 'inputs' } }

function show(state: keyof typeof STATES, mode: 'guided' | 'expert' = 'guided') {
  useUIStore.setState({ uiMode: mode })
  vi.mocked(api.getRun).mockResolvedValue(STATES[state])
  return render(<AssistantResultCard result={START} disabled={false} onRequest={vi.fn()} />)
}

const card = () => screen.getByTestId('run-monitor').textContent ?? ''

beforeEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.mocked(api.presentRun).mockResolvedValue()
  useChatStore.setState({ pending: null, streaming: false, requestQueue: [], lastRequest: null, sessionId: 's1' })
  useUIStore.setState({ activeSlidePanel: null, flowReturn: null })
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
      ['The model has no errors · 0 · pass', 'The model is ready to solve · pass'])
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

  it.each([
    ['gate_fail', /^Check inputs failed: 1 rule did not pass · Fix in Inputs › then Check again ›$/],
    ['over_budget', /^Cost budget used up: the run stopped: Review the results so far ›$/],
    ['expired', /^Unattended approval expired: Continue step by step ›$/],
    ['solver_error', /^Solve did not finish: Open the solve log ›$/],
    ['out_of_date', /^Inputs out of date: Review a new brief ›$/],
  ] as const)('says the %s failure in words with a › fix', async (state, words) => {
    show(state)
    expect((await screen.findByTestId('run-failure')).textContent).toMatch(words)
  })

  it('a fix link opens its panel on the click, with Back to the origin; the card never switches on its own', async () => {
    show('finished')
    await screen.findByTestId('run-finished')
    expect(useUIStore.getState().activeSlidePanel).toBeNull()
    fireEvent.click(screen.getByTestId('run-report-link'))
    expect(useUIStore.getState().activeSlidePanel).toBe('reports')
    expect(useUIStore.getState().flowReturn).not.toBeNull()
  })

  it('paused: names the stage and how to resume; Resume goes on', async () => {
    vi.mocked(api.resumeRun).mockResolvedValue(STATES.gate_pass)
    show('paused')
    const failure = await screen.findByTestId('run-failure')
    expect(failure.textContent).toMatch(/^Paused at Solve: Resume ›The unattended approval keeps running until it expires\.$/)
    fireEvent.click(screen.getByRole('button', { name: 'Resume ›' }))
    await waitFor(() => expect(api.resumeRun).toHaveBeenCalledWith(TASK_ID, 's1'))
  })

  it('Stop says what it keeps; a stopped card names the stage, never an internal status', async () => {
    vi.mocked(api.stopRun).mockResolvedValue(STATES.stopped)
    show('gate_pass')
    expect((await screen.findByTestId('run-stop-note')).textContent)
      .toBe('Stop keeps the edits applied so far and the results so far; it cannot be undone.')
    fireEvent.click(screen.getByTestId('run-stop'))
    const stopped = await screen.findByTestId('run-stopped')
    expect(stopped.textContent).toMatch(/^Stopped at Solve: the edits applied so far and the results so far are kept\./)
    expect(card()).not.toMatch(/cancelled/i)
    expect(screen.queryByTestId('run-pause')).toBeNull()
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
