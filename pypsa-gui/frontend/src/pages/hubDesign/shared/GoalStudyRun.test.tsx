// Guided-mode spec §5.3, now in section 9 (design §3.3): Run posts exactly what the
// Expert panel's `buildGoalFreeStudyBody` builds for the same inputs — no goal
// field, the backend fills them from the stored goal (W3b-R); VOLL ≤ 0
// blocks the run and offers the assistant fix; running / failed / aborted are
// read from the study record (P24-BE gate N2).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { resultsApi, simulationApi } from '../../../api/simulation'
import { useUIStore } from '../../../store/uiStore'
import { buildGoalFreeStudyBody, EMPTY_PACK_FORM, formFromTemplate } from '../../results/EhReferenceDesignPanel'
import { goal } from '../../../test/fixtures/reliabilityGoal'
import { HUB_DESIGN_INITIAL, useHubDesignStore } from '../hubDesignStore'
import { DC_TEMPLATE, readiness } from '../testFixtures'
import { GoalStudyRun, RELIABILITY_OUT_OF_SCOPE } from './GoalStudyRun'

vi.mock('../../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/simulation')>()
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getEhStudy: vi.fn(), getEhReadiness: vi.fn(), getEhTemplate: vi.fn(),
      startEhStudy: vi.fn(), abortEhStudy: vi.fn(), getFmeaModes: vi.fn(),
    },
    simulationApi: { ...actual.simulationApi, getSolverConfig: vi.fn() },
  }
})

// The scope (W4a): unread by default, so Run shows; one test reads a scope with Reliability off.
const getFlowScope = vi.fn()
const getReliabilityGoal = vi.fn()
vi.mock('../../../api/flow', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/flow')>()
  return { ...actual, getFlowScope: (...a: unknown[]) => getFlowScope(...a),
    getReliabilityGoal: (...a: unknown[]) => getReliabilityGoal(...a) }
})

beforeEach(() => {
  getFlowScope.mockReturnValue(new Promise(() => {}))
  useUIStore.setState({ currentProject: 'Demo', assistantDockOpen: false })
  useHubDesignStore.setState({ ...HUB_DESIGN_INITIAL, project: 'Demo', ready: true, step: 'goal' })
  getReliabilityGoal.mockResolvedValue(goal({ site_archetype: ['weak_flexible', 'template'] }))
  vi.mocked(resultsApi.getEhStudy).mockResolvedValue(null)
  vi.mocked(resultsApi.getEhTemplate).mockResolvedValue(DC_TEMPLATE)
  vi.mocked(resultsApi.getEhReadiness).mockResolvedValue(readiness())
  vi.mocked(resultsApi.startEhStudy).mockResolvedValue({ status: 'running' })
  vi.mocked(simulationApi.getSolverConfig).mockResolvedValue({ voll: 5000 } as never)
  vi.mocked(resultsApi.getFmeaModes).mockResolvedValue({ per_mode: [], sweep_status: null } as never)
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><GoalStudyRun /></QueryClientProvider>)
  return userEvent.setup()
}


describe('GoalStudyRun run', () => {
  it('posts the Expert panel\'s body for the same inputs, without the goal fields', async () => {
    getReliabilityGoal.mockResolvedValue(goal({ target_lole_h: [3, 'stored'],
      ens_cap_permyriad: [5, 'stored'], mc_draws: [800, 'stored'], site_archetype: ['weak_flexible', 'stored'] }))
    const user = mount()
    await waitFor(() => expect((screen.getByTestId('hub-run-start') as HTMLButtonElement).disabled).toBe(false))
    await user.click(screen.getByTestId('hub-run-start'))
    const expected = buildGoalFreeStudyBody('weak_flexible', formFromTemplate(DC_TEMPLATE)).body
    await waitFor(() => expect(resultsApi.startEhStudy).toHaveBeenCalledTimes(1))
    const sent = vi.mocked(resultsApi.startEhStudy).mock.calls[0][0]
    expect(sent).toEqual(expected)
    // the site type's rules still apply (weak_flexible keeps the import cap) …
    expect(sent.pack_overrides).toEqual({ import_p_nom_mw: 40 })
    // … and no goal field rides: the backend fills them from the stored goal
    expect(sent).not.toHaveProperty('archetype')
    expect(sent).not.toHaveProperty('mc')
  })

  it('an own network runs from the empty pack form', async () => {
    vi.mocked(resultsApi.getEhTemplate).mockResolvedValue(null)
    getReliabilityGoal.mockResolvedValue(goal())
    vi.mocked(resultsApi.getEhReadiness).mockResolvedValue(readiness({
      pack_defaults: { target_lole_h: null, ens_cap_permyriad: 10, certification_metric: 'none' } }))
    const user = mount()
    await waitFor(() => expect((screen.getByTestId('hub-run-start') as HTMLButtonElement).disabled).toBe(false))
    await user.click(screen.getByTestId('hub-run-start'))
    await waitFor(() => expect(resultsApi.startEhStudy).toHaveBeenCalledWith(
      buildGoalFreeStudyBody('strong_grid', EMPTY_PACK_FORM).body))
    expect(vi.mocked(resultsApi.startEhStudy).mock.calls[0][0]).toEqual({})
  })

  it('an unreadable goal blocks the run with a message, never a guessed site type', async () => {
    getReliabilityGoal.mockRejectedValue(new Error('500'))
    mount()
    await waitFor(() => expect(screen.getByTestId('hub-run-blocked')).toBeTruthy())
    expect(screen.getByTestId('hub-run-blocked').textContent).toContain('The reliability goal could not be read.')
    expect((screen.getByTestId('hub-run-start') as HTMLButtonElement).disabled).toBe(true)
  })

  it('waits for the goal before it can run', async () => {
    getReliabilityGoal.mockReturnValue(new Promise(() => {}))
    mount()
    await new Promise(r => setTimeout(r, 20))
    expect((screen.getByTestId('hub-run-start') as HTMLButtonElement).disabled).toBe(true)
  })

  it('a refused start (409) shows the backend sentence', async () => {
    vi.mocked(resultsApi.startEhStudy).mockRejectedValue(Object.assign(new Error('409'),
      { response: { status: 409, data: { detail: 'an FMEA sweep is running — wait for it' } } }))
    const user = mount()
    await waitFor(() => expect((screen.getByTestId('hub-run-start') as HTMLButtonElement).disabled).toBe(false))
    await user.click(screen.getByTestId('hub-run-start'))
    await waitFor(() => expect(screen.getByTestId('hub-run-blocked').textContent)
      .toContain('an FMEA sweep is running — wait for it'))
  })

  it('VOLL ≤ 0 disables Run', async () => {
    vi.mocked(simulationApi.getSolverConfig).mockResolvedValue({ voll: 0 } as never)
    mount()
    await waitFor(() => expect((screen.getByTestId('hub-run-start') as HTMLButtonElement).disabled).toBe(true))
  })

  it('with Reliability off, Run is disabled and Add puts it back in place (W4a)', async () => {
    getFlowScope.mockResolvedValue({
      scope: { version: 1, groups: ['capacity', 'handoff'], source: 'project', preset_id: 'eh_datacenter',
        options: { capacity: ['sizing', 'dispatch'], grid: [], control: [],
          reliability: ['goal_study'], commercial: [], handoff: ['reports'] } },
      cards: [{ id: 'reliability', label: 'Reliability', locked_reason: null, intended: ['goal_study'],
        sections: ['assets', 'scenarios'], chip: 'Reliability',
        options: [{ id: 'goal_study', label: 'Reliability goal & study', status: 'available', reason: null }] }],
      preset_id: 'eh_datacenter', preset_label: 'Data Center Energy Hub',
      preset: { version: 1, groups: [], options: {} },
    })
    mount()
    expect((await screen.findByTestId('hub-run-out-of-scope-add')).textContent)
      .toBe('Add Reliability goal & study')
    const run = screen.getByTestId('hub-run-start') as HTMLButtonElement
    expect(run.disabled).toBe(true)
    expect(run.title).toBe(RELIABILITY_OUT_OF_SCOPE)
  })
})

describe('GoalStudyRun where Reliability is not offered', () => {
  it('the disabled Run title gives the registry reason, not "add…"', async () => {
    getFlowScope.mockResolvedValue({
      scope: { version: 1, groups: ['capacity', 'handoff'], source: 'project', preset_id: 'eh_ipp_hybrid',
        options: { capacity: ['sizing', 'dispatch'], grid: [], control: [],
          reliability: [], commercial: [], handoff: ['reports'] } },
      cards: [{ id: 'reliability', label: 'Reliability', locked_reason: null, intended: [],
        sections: ['assets', 'scenarios'], chip: 'Reliability',
        options: [{ id: 'goal_study', label: 'Reliability goal & study', status: 'not_yet_available',
          reason: 'Nothing to make adequate: this site only generates and has no load.' }] }],
      preset_id: 'eh_ipp_hybrid', preset_label: 'IPP', preset: { version: 1, groups: [], options: {} },
    })
    mount()
    const run = await screen.findByTestId('hub-run-start') as HTMLButtonElement
    await waitFor(() => expect(run.title).toBe(
      'Not offered for this site: Nothing to make adequate: this site only generates and has no load.'))
  })
})

describe('GoalStudyRun study states (from the study record, N2)', () => {
  it('running: the card\'s own running text and Abort — never the review\'s message', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({ status: 'running', budget_solves: 40 })
    vi.mocked(resultsApi.abortEhStudy).mockResolvedValue({ status: 'running', aborting: true })
    const user = mount()
    const t = await screen.findByTestId('hub-run-running')
    expect(t.textContent).toBe('Studying… (up to 40 calculation steps) · 0:00 elapsed')
    expect(t.textContent).not.toMatch(/poll|get_adequacy_results|solves/)
    expect((screen.getByTestId('hub-run-start') as HTMLButtonElement).disabled).toBe(true)
    await user.click(screen.getByTestId('hub-run-abort'))
    expect(resultsApi.abortEhStudy).toHaveBeenCalled()
  })

  it('running text: plain words, and never "0 of N" when the count is missing', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({ status: 'running', budget_solves: 40,
      report: { archetype: 'weak_flexible', pack_hash: 'h', assumptions_hash: 'a',
        pipeline: { budget_solves: 40 } } })
    mount()
    const t = await screen.findByTestId('hub-run-running')
    expect(t.textContent).toBe('Studying… (up to 40 calculation steps) · 0:00 elapsed')
  })

  it('running text with a count: "n of N calculation steps"', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({ status: 'running',
      report: { archetype: 'weak_flexible', pack_hash: 'h', assumptions_hash: 'a',
        pipeline: { budget_solves: 40, solves_consumed: 12 } } })
    mount()
    expect((await screen.findByTestId('hub-run-running')).textContent)
      .toBe('Studying… 12 of 40 calculation steps · 0:00 elapsed')
  })

  it('failed: the error and Run again', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(
      { status: 'failed', error: 'solver infeasible', report: null })
    mount()
    await waitFor(() => expect(screen.getByTestId('hub-run-error').textContent)
      .toContain('solver infeasible'))
    expect(screen.getByTestId('hub-run-start').textContent).toBe('Run again')
  })

  it('aborted: says so and offers Run again', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({ status: 'aborted', report: null })
    mount()
    await waitFor(() => expect(screen.getByTestId('hub-run-error').textContent).toMatch(/Stopped/))
    expect(screen.getByTestId('hub-run-start').textContent).toBe('Run again')
  })
})

// P24-FE re-gate note 2 (P25 step 0): when only the template read failed the
// card cannot know the template's recommended settings, so Run must not
// silently start the default-site-type study. It is disabled with a plain
// reason and a Retry that re-reads the template; once the read recovers Run
// posts the template's body.

// card cannot know the template's recommended settings, so Run must not
// silently start the default-site-type study. It is disabled with a plain
// reason and a Retry that re-reads the template; once the read recovers Run
// posts the template's body.
describe('GoalStudyRun when the template read failed', () => {
  it('disables Run with a plain reason and a Retry that recovers', async () => {
    vi.mocked(resultsApi.getEhTemplate).mockRejectedValueOnce(new Error('500'))
    const user = mount()
    const reason = await screen.findByTestId('hub-run-template-error')
    expect(reason.textContent).toMatch(/recommended settings could not be read/)
    expect(reason.textContent).not.toMatch(/eh_template|500/)
    expect((screen.getByTestId('hub-run-start') as HTMLButtonElement).disabled).toBe(true)
    // P30 (B8): the disabled Run says why.
    expect(screen.getByTestId('hub-run-start').getAttribute('title'))
      .toBe('The template could not be read — retry below.')
    await user.click(screen.getByTestId('hub-run-start'))
    expect(resultsApi.startEhStudy).not.toHaveBeenCalled()

    await user.click(screen.getByTestId('hub-run-template-retry'))
    await waitFor(() => expect(screen.queryByTestId('hub-run-template-error')).toBeNull())
    await waitFor(() => expect((screen.getByTestId('hub-run-start') as HTMLButtonElement)
      .disabled).toBe(false))
    expect(screen.getByTestId('hub-run-start').getAttribute('title')).toBeNull()
    await user.click(screen.getByTestId('hub-run-start'))
    await waitFor(() => expect(resultsApi.startEhStudy).toHaveBeenCalledTimes(1))
    expect(vi.mocked(resultsApi.startEhStudy).mock.calls[0][0])
      .toEqual(buildGoalFreeStudyBody('weak_flexible', formFromTemplate(DC_TEMPLATE)).body)
  })

  it('an own network (template read → null) runs as before', async () => {
    vi.mocked(resultsApi.getEhTemplate).mockResolvedValue(null)
    mount()
    await waitFor(() => expect((screen.getByTestId('hub-run-start') as HTMLButtonElement)
      .disabled).toBe(false))
    expect(screen.queryByTestId('hub-run-template-error')).toBeNull()
  })
})
