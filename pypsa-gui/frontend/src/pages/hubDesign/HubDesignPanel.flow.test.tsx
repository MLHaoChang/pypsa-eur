// Guided-mode spec §5.4 / §5.6: the hub-design state machine. The five flow
// states render the right rail states and card; a project opens on the step
// §5.4 names; running → done moves the rail to Results unless the user moved
// it; done → running moves it to Goal. Stale comes from the review's boolean
// (§4.1, gate note N8); failed / aborted from the study record (N2).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { resultsApi, simulationApi } from '../../api/simulation'
import * as flowApi from '../../api/flow'
import { guidesApi } from '../../components/GuidedTour'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import { HUB_DESIGN_INITIAL, useHubDesignStore } from './hubDesignStore'
import { BLOCKED_NO_PROJECT, BLOCKED_NO_STUDY } from './flow'
import { DC_TEMPLATE, REPORT, readiness, review } from './testFixtures'
import { goal } from '../../test/fixtures/reliabilityGoal'
import HubDesignPanel from './HubDesignPanel'
import guide from '../../../../backend/data/guides/eh_fmea_guide.json'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getEhStudy: vi.fn(), getEhReview: vi.fn(), getEhReadiness: vi.fn(),
      getEhTemplate: vi.fn(), getEhReferenceDesign: vi.fn(),
    },
    simulationApi: { ...actual.simulationApi, getSolverConfig: vi.fn() },
  }
})

// The rail's marks come from /flow now, not from the study (design §3.4): the
// project section is complete, the connection has something to check, the
// scenarios section is still to do.
vi.mock('../../api/flow', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/flow')>()
  return { ...actual, getProjectFlow: vi.fn(), getReliabilityGoal: vi.fn() }
})
const STATES: Record<string, string | null> = {
  project: 'complete', carriers: 'complete', connection: 'warning', loads: 'complete',
  assets: 'complete', profiles: 'complete', commercial: 'not_needed', scenarios: 'incomplete',
  readiness: null, results: null,
}
function flowPayload(): flowApi.ProjectFlow {
  return {
    sections: flowApi.FLOW_SECTIONS.map(section => ({
      section, label: section, state: STATES[section], required: true, items: [], summary: null,
    })),
    run_ready: true, run_blocker: null, run_request: { optimisation: true, study: true },
    verdicts: { optimisation: { ready: true, blocker: null }, study: null },
    notes: [], deferred: false, deferred_reason: null,
  } as unknown as flowApi.ProjectFlow
}

const RUNNING = { status: 'running', archetype: 'weak_flexible' as const }
const DONE = { status: 'done', archetype: 'weak_flexible' as const, report: REPORT }

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo', activeSection: null, fieldDrawer: null, uiMode: 'guided' })
  vi.mocked(flowApi.getProjectFlow).mockResolvedValue(flowPayload())
  vi.mocked(flowApi.getReliabilityGoal).mockResolvedValue(goal({ site_archetype: ['weak_flexible', 'template'] }))
  useHubDesignStore.setState({ ...HUB_DESIGN_INITIAL })
  vi.spyOn(guidesApi, 'getGuide').mockRejectedValue(new Error('offline'))
  vi.mocked(resultsApi.getEhStudy).mockResolvedValue(null)
  vi.mocked(resultsApi.getEhReview).mockResolvedValue(null)
  vi.mocked(resultsApi.getEhReadiness).mockResolvedValue(readiness())
  vi.mocked(resultsApi.getEhTemplate).mockResolvedValue(DC_TEMPLATE)
  vi.mocked(resultsApi.getEhReferenceDesign).mockResolvedValue(null)
  vi.mocked(simulationApi.getSolverConfig).mockResolvedValue({ voll: 5000 } as never)
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.clearAllMocks() })

let client: QueryClient
function mount() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><HubDesignPanel /></QueryClientProvider>)
  return userEvent.setup()
}
const readinessRail = () => screen.getByTestId('hub-rail-section-readiness')
const rail = (s: string) => screen.getByTestId(`hub-rail-step-${s}`)
const railStates = () => Object.fromEntries(['start', 'site', 'goal', 'results', 'improve']
  .map(s => [s, rail(s).getAttribute('data-state')]))
/** Drive the study record directly (the 2 s poll is not waited for). */
function setStudy(payload: unknown) {
  vi.mocked(resultsApi.getEhStudy).mockResolvedValue(payload as never)
  act(() => { client.setQueryData(nk('Demo', 'results', 'eh_study'), payload) })
}

describe('flowState → rail and card (§5.4)', () => {
  it('no_project: Start only; every other step blocked with its reason', async () => {
    useUIStore.setState({ currentProject: null })
    mount()
    await screen.findByTestId('hub-card-start')
    expect(railStates()).toEqual({ start: 'current', site: 'blocked', goal: 'blocked',
      results: 'blocked', improve: 'blocked' })
    expect(rail('site').getAttribute('title')).toBe(BLOCKED_NO_PROJECT)
    expect((rail('goal') as HTMLButtonElement).disabled).toBe(true)
    expect(resultsApi.getEhStudy).not.toHaveBeenCalled()
  })

  it('no_study on a template project: Start ✓, opens on Site; Improve blocked, Results reachable', async () => {
    mount()
    await screen.findByTestId('hub-card-site')
    expect(railStates()).toEqual({ start: 'done', site: 'current', goal: 'todo',
      results: 'todo', improve: 'blocked' })
    expect(rail('improve').getAttribute('title')).toBe(BLOCKED_NO_STUDY)
    expect((rail('results') as HTMLButtonElement).disabled).toBe(false)
  })

  it('no_study on an own network: opens on Start (not ✓)', async () => {
    vi.mocked(resultsApi.getEhTemplate).mockResolvedValue(null)
    mount()
    await screen.findByTestId('hub-card-start')
    expect(railStates()).toMatchObject({ start: 'current', site: 'warning', results: 'todo' })
  })

  it('failed without a report is no_study; Readiness and run shows the error and Run again', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({ status: 'failed', error: 'boom', report: null })
    // the route answers 204 for a failed study (N2): the review is not asked
    const user = mount()
    await screen.findByTestId('hub-card-site')
    expect(railStates()).toMatchObject({ results: 'todo', improve: 'blocked' })
    await user.click(readinessRail())
    expect((await screen.findByTestId('hub-run-error')).textContent).toContain('boom')
    expect(screen.getByTestId('hub-run-start').textContent).toBe('Run again')
    expect(resultsApi.getEhReview).not.toHaveBeenCalled()
  })

  it('running: opens on Readiness and run with a spinner; Improve blocked, Results says it is running', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(RUNNING)
    const user = mount()
    await screen.findByTestId('hub-run-running')
    expect(railStates()).toMatchObject({ start: 'done', site: 'warning', results: 'todo', improve: 'blocked' })
    expect(screen.getByTestId('hub-rail-spinner')).toBeTruthy()
    await user.click(rail('results'))
    expect((await screen.findByTestId('hub-card-results')).textContent).toContain('The study is running')
  })

  it('done: opens on Results; every step enabled', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(DONE)
    vi.mocked(resultsApi.getEhReview).mockResolvedValue(review())
    mount()
    await screen.findByTestId('hub-card-results')
    // Marks follow /flow (inputs), not the study: the study does not tick them.
    expect(railStates()).toEqual({ start: 'done', site: 'warning', goal: 'todo',
      results: 'current', improve: 'todo' })
    await screen.findByTestId('hub-results-verdict')
    expect(screen.queryByTestId('hub-results-stale')).toBeNull()
  })

  it('stale: as done plus the banner, from review.stale', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(DONE)
    vi.mocked(resultsApi.getEhReview).mockResolvedValue(review({ stale: true }))
    mount()
    await screen.findByTestId('hub-results-stale')
    expect(railStates()).toMatchObject({ results: 'current', improve: 'todo' })
  })

  it('aborted with a partial report counts as done (the review reads it)', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({ status: 'aborted', report: REPORT })
    vi.mocked(resultsApi.getEhReview).mockResolvedValue(review())
    mount()
    await screen.findByTestId('hub-card-results')
  })
})

describe('auto-advance (§5.6)', () => {
  it('running → done moves the rail to Results', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(RUNNING)
    vi.mocked(resultsApi.getEhReview).mockResolvedValue(review())
    mount()
    await screen.findByTestId('hub-run-running')
    setStudy(DONE)
    await screen.findByTestId('hub-card-results')
    expect(useHubDesignStore.getState().step).toBe('results')
  })

  it('a manual rail click suppresses it, once', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(RUNNING)
    vi.mocked(resultsApi.getEhReview).mockResolvedValue(review())
    const user = mount()
    await screen.findByTestId('hub-run-running')
    await user.click(rail('site'))
    expect(useHubDesignStore.getState().userMovedRail).toBe(true)
    setStudy(DONE)
    await waitFor(() => expect(useHubDesignStore.getState().userMovedRail).toBe(false))
    expect(useHubDesignStore.getState().step).toBe('site')
    expect(screen.getByTestId('hub-card-site')).toBeTruthy()
  })

  it('done → running moves the rail to Readiness and run, where the Run is', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(DONE)
    vi.mocked(resultsApi.getEhReview).mockResolvedValue(review())
    const user = mount()
    await screen.findByTestId('hub-card-results')
    await user.click(rail('improve'))
    setStudy(RUNNING)
    await screen.findByTestId('hub-run-running')
    expect(useUIStore.getState().activeSection).toBe('readiness')
  })

  it('a new project resets the store and derives its own step', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(DONE)
    vi.mocked(resultsApi.getEhReview).mockResolvedValue(review())
    mount()
    await screen.findByTestId('hub-card-results')
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(null)
    vi.mocked(resultsApi.getEhTemplate).mockResolvedValue(null)
    act(() => { useUIStore.setState({ currentProject: 'Other' }) })
    await screen.findByTestId('hub-card-start')
    expect(useHubDesignStore.getState()).toMatchObject({ project: 'Other', step: 'start' })
    // The goal is the project's own (W3b-R): read again for the new project.
    await waitFor(() => expect(flowApi.getReliabilityGoal).toHaveBeenCalledWith('Other'))
    expect(useHubDesignStore.getState()).not.toHaveProperty('archetype')
    expect(useHubDesignStore.getState()).not.toHaveProperty('loleTarget')
  })

  it('a template project shows the goal\'s site type, the template\'s as its fallback (W3b-R)', async () => {
    vi.mocked(resultsApi.getEhTemplate).mockResolvedValue(
      { ...DC_TEMPLATE, pack_overrides: { import_p_nom_mw: 40, target_lole_h: 2, ens_cap_permyriad: 4 } })
    mount()
    await screen.findByTestId('hub-card-site')
    await waitFor(() => expect((screen.getByTestId('hub-site-type') as HTMLSelectElement).value)
      .toBe('weak_flexible'))
    expect(screen.getByTestId('hub-site-type-source').textContent).toMatch(/template's choice/)
    await waitFor(() => expect(resultsApi.getEhReadiness).toHaveBeenCalled())
    expect(vi.mocked(resultsApi.getEhReadiness).mock.calls.every(c => c[0] === 'weak_flexible')).toBe(true)
  })
})

// P24-FE gate B2: a first-time user opens the tour on the Site card before
// any study. Every step it shows must be on screen (no "not on screen" dead
// end), and the tour's own rail clicks are not manual moves — the rail still
// jumps to Results when a study then finishes.
describe('hub_design tour before any study (gate B2)', () => {
  it('walks every shown step with its target on screen, then auto-advance still works', async () => {
    vi.mocked(guidesApi.getGuide).mockResolvedValue(guide as never)
    const rect = vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue(
      { top: 10, left: 10, width: 50, height: 20, right: 60, bottom: 30, x: 10, y: 10,
        toJSON: () => ({}) } as DOMRect)
    Element.prototype.scrollIntoView = vi.fn()
    vi.mocked(resultsApi.getEhReview).mockResolvedValue(review())
    const user = mount()
    await screen.findByTestId('hub-card-site')
    await user.click(screen.getByTestId('hub-guide-button'))
    const seen: string[] = []
    for (let i = 0; i < 12; i++) {
      const tour = await screen.findByTestId('guide-tour')
      const target = tour.getAttribute('data-step-target')!
      // a reveal re-renders on the next commit; GuidedTour looks again at 60 ms
      await waitFor(() => expect(screen.queryByTestId('guide-step-missing')).toBeNull(), { timeout: 500 })
      expect(screen.queryByTestId(target)).not.toBeNull()
      seen.push(target)
      const next = screen.getByTestId('guide-next')
      const last = next.textContent === 'Done'
      await user.click(next)
      if (last) break
      await waitFor(() => expect(screen.getByTestId('guide-tour')
        .getAttribute('data-step-target')).not.toBe(target))
    }
    expect(seen).toEqual(['hub-rail', 'hub-start-templates', 'hub-site-readiness',
      'hub-site-type', 'hub-goal-lole', 'hub-run-start'])
    expect(screen.queryByTestId('guide-tour')).toBeNull()
    // the tour's reveal clicks did not count as the user moving the rail
    expect(useHubDesignStore.getState().userMovedRail).toBe(false)
    setStudy(RUNNING)
    await screen.findByTestId('hub-run-running')
    setStudy(DONE)
    await screen.findByTestId('hub-card-results')
    rect.mockRestore()
  })
})

// P24-FE re-gate B4: a first read that fails used to flip the card between
// "Loading the project…" and mounted, each remount refetching — an endless
// request loop. An errored read counts as settled, the card stays mounted, a
// plain error line offers Retry, and the request count stays bounded.
describe('a failed first read (re-gate B4)', () => {
  const boom = () => Object.assign(new Error('Request failed with status code 500'),
    { response: { status: 500, data: { detail: 'boom' } } })

  it('eh_study rejects: error line + card, a bounded number of requests, Retry recovers', async () => {
    vi.mocked(resultsApi.getEhStudy).mockRejectedValue(boom())
    vi.mocked(resultsApi.getEhReview).mockResolvedValue(review())
    const user = mount()
    const line = await screen.findByTestId('hub-load-error')
    expect(line.textContent).toMatch(/could not be read/i)
    expect(screen.getByTestId('hub-card-site')).toBeTruthy()
    await new Promise(r => setTimeout(r, 500))
    expect(vi.mocked(resultsApi.getEhStudy).mock.calls.length).toBeLessThanOrEqual(3)
    expect(screen.queryByText('Loading the project…')).toBeNull()
    // the server recovers with a finished study: Retry clears the line and
    // the rail moves to where that study belongs
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(DONE)
    await user.click(screen.getByTestId('hub-load-retry'))
    await screen.findByTestId('hub-card-results')
    expect(screen.queryByTestId('hub-load-error')).toBeNull()
  })

  it('eh_template rejects: error line + card, a bounded number of requests, Retry recovers', async () => {
    vi.mocked(resultsApi.getEhTemplate).mockRejectedValue(boom())
    const user = mount()
    await screen.findByTestId('hub-load-error')
    expect(screen.getByTestId('hub-card-start')).toBeTruthy()   // read as an own network
    await new Promise(r => setTimeout(r, 500))
    expect(vi.mocked(resultsApi.getEhTemplate).mock.calls.length).toBeLessThanOrEqual(3)
    expect(vi.mocked(resultsApi.getEhStudy).mock.calls.length).toBeLessThanOrEqual(3)
    vi.mocked(resultsApi.getEhTemplate).mockResolvedValue(DC_TEMPLATE)
    await user.click(screen.getByTestId('hub-load-retry'))
    await screen.findByTestId('hub-card-site')                  // now a template project
    expect(screen.queryByTestId('hub-load-error')).toBeNull()
  })

  // P30 (B8): the line names what failed — the first of study state,
  // template, readiness — and Goal's Run waits for the template.
  it('template 500 → the line names the template, hub-run-start disabled', async () => {
    vi.mocked(resultsApi.getEhTemplate).mockRejectedValue(boom())
    const user = mount()
    const line = await screen.findByTestId('hub-load-error')
    expect(line.textContent).toMatch(
      /^This project's template could not be read from the server, so the steps below may be incomplete\./)
    await user.click(readinessRail())
    const run = await screen.findByTestId('hub-run-start') as HTMLButtonElement
    expect(run.disabled).toBe(true)
    expect(run.title).toBe('The template could not be read — retry below.')
  })

  it('study 500 → the line names the study state', async () => {
    vi.mocked(resultsApi.getEhStudy).mockRejectedValue(boom())
    mount()
    const line = await screen.findByTestId('hub-load-error')
    expect(line.textContent).toMatch(/^This project's study state could not be read from the server/)
  })

  it('study and template both fail → the study state is named (first in order)', async () => {
    vi.mocked(resultsApi.getEhStudy).mockRejectedValue(boom())
    vi.mocked(resultsApi.getEhTemplate).mockRejectedValue(boom())
    mount()
    const line = await screen.findByTestId('hub-load-error')
    expect(line.textContent).toMatch(/^This project's study state could not be read/)
  })

  it('readiness 500 → the line names the readiness check; Retry re-reads it', async () => {
    vi.mocked(resultsApi.getEhReadiness).mockRejectedValue(boom())
    const user = mount()
    await screen.findByTestId('hub-card-site')
    const line = await screen.findByTestId('hub-load-error')
    expect(line.textContent).toMatch(/^This project's readiness check could not be read from the server/)
    vi.mocked(resultsApi.getEhReadiness).mockResolvedValue(readiness())
    const calls = vi.mocked(resultsApi.getEhReadiness).mock.calls.length
    await user.click(screen.getByTestId('hub-load-retry'))
    await waitFor(() => expect(screen.queryByTestId('hub-load-error')).toBeNull())
    expect(vi.mocked(resultsApi.getEhReadiness).mock.calls.length).toBeGreaterThan(calls)
  })

  it('the hub reads the study and template quietly (the card shows the error, no toast storm)', async () => {
    mount()
    await screen.findByTestId('hub-card-site')
    expect(resultsApi.getEhStudy).toHaveBeenCalledWith({ quiet: true })
    expect(resultsApi.getEhTemplate).toHaveBeenCalledWith('Demo', { quiet: true })
  })
})

describe('tour anchors', () => {
  it('renders the rail and the guide button', async () => {
    mount()
    await screen.findByTestId('hub-card-site')
    expect(screen.getByTestId('hub-rail')).toBeTruthy()
    expect(screen.getByTestId('hub-guide-button')).toBeTruthy()
    expect(screen.getByTestId('hub-design-panel')).toBeTruthy()
  })
})

// P33b 10b: the panel watches the polled edit counter and re-reads the study
// record and the review when it moves, so the Results card's edited banner
// appears without a reload.
describe('HubDesignPanel re-reads the study after an edit (P33b)', () => {
  it('a polled network_revision change invalidates the two hub queries once', async () => {
    useUIStore.setState({ uiMode: 'guided' })
    const { networkApi } = await import('../../api/network')
    vi.spyOn(networkApi, 'undoInfo').mockResolvedValue({ depth: 0, unsaved: false, network_revision: 2 })
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(DONE as never)
    vi.mocked(resultsApi.getEhReview).mockResolvedValue(review())
    mount()
    await screen.findByTestId('hub-card-results')
    await waitFor(() => expect(networkApi.undoInfo).toHaveBeenCalled())
    const spy = vi.spyOn(client, 'invalidateQueries')
    await act(async () => {
      client.setQueryData(nk('Demo', 'undoInfo'), { depth: 1, unsaved: true, network_revision: 3 })
      await new Promise(r => setTimeout(r, 5))
    })
    const keys = spy.mock.calls.map(([f]) => JSON.stringify(f?.queryKey))
    expect(keys.filter(k => k === JSON.stringify(nk('Demo', 'results', 'eh_study')))).toHaveLength(1)
    expect(keys.filter(k => k === JSON.stringify(nk('Demo', 'results', 'eh_review')))).toHaveLength(1)
  })
})

describe('HubDesignPanel in Expert never watches the edit counter (P33b, D-4)', () => {
  it('no undo/info read and no hub invalidation from the hook', async () => {
    useUIStore.setState({ uiMode: 'expert' })
    const { networkApi } = await import('../../api/network')
    vi.spyOn(networkApi, 'undoInfo').mockResolvedValue({ depth: 0, unsaved: false, network_revision: 2 })
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(DONE as never)
    vi.mocked(resultsApi.getEhReview).mockResolvedValue(review())
    mount()
    await screen.findByTestId('hub-card-results')
    await new Promise(r => setTimeout(r, 30))
    expect(networkApi.undoInfo).not.toHaveBeenCalled()
  })
})

describe('the rail is the ten flow sections', () => {
  const ids = ['project', 'carriers', 'connection', 'loads', 'assets', 'profiles', 'commercial',
    'scenarios', 'readiness', 'results']

  it('lists them in flow order with the state /flow gives each', async () => {
    mount()
    await screen.findByTestId('hub-card-site')
    const buttons = Array.from(screen.getByTestId('hub-rail').querySelectorAll('button'))
      .filter(b => b.getAttribute('data-testid') !== 'hub-rail-step-improve')
    expect(buttons).toHaveLength(10)
    expect(buttons.map(b => b.textContent?.replace(/^\d+/, ''))).toEqual([
      'Project & site', 'Carriers & processes', 'Connection & grid', 'Loads & demand', 'Assets & sizing',
      'Time series & profiles', 'Commercial & parties', 'Scenarios & optimisation', 'Readiness and run',
      'Results & reports'])
    expect(ids).toHaveLength(10)
  })

  it('scrolls the current step into view when a link jumps to it (a "… ›" goal link)', async () => {
    const scrolled: string[] = []
    const spy = vi.spyOn(Element.prototype, 'scrollIntoView').mockImplementation(
      function (this: Element) { scrolled.push(this.getAttribute('data-testid') ?? '') })
    try {
      mount()
      await screen.findByTestId('hub-card-site')
      scrolled.length = 0
      act(() => { useUIStore.setState({ activeSection: 'scenarios' }) })
      await screen.findByTestId('hub-card-goal')
      await waitFor(() => expect(scrolled).toContain('hub-rail-step-goal'))
    } finally { spy.mockRestore() }
  })

  it('a section without a bespoke card shows the generic card and its open items, with a way to Expert', async () => {
    const payload = flowPayload()
    const loads = payload.sections.find(x => x.section === 'loads')!
    loads.state = 'incomplete'
    loads.items = [{
      code: 'no_load', severity: 'error', message: 'Add a demand', source: 'validation', blocking: false,
      acknowledged: false, card: null, in_scope: true,
      target: { section: 'loads', panel: null, component: 'Load', entity: 'dc_it', field: 'p_set' },
    }]
    vi.mocked(flowApi.getProjectFlow).mockResolvedValue(payload)
    const user = mount()
    await screen.findByTestId('hub-card-site')
    await user.click(screen.getByTestId('hub-rail-section-loads'))
    expect(await screen.findByTestId('hub-card-loads')).toBeTruthy()
    expect(useUIStore.getState().activeSection).toBe('loads')
    const item = await screen.findByTestId('section-item')
    expect(item.textContent).toContain('Add a demand')
    expect(item.textContent).toContain("Loads & demand › Load 'dc_it'")
    expect(item.textContent).not.toContain('p_set')
    // The primary action opens the field here, in Guided (design §3.5).
    await user.click(screen.getByTestId('section-item-open'))
    expect(useUIStore.getState().uiMode).toBe('guided')
    expect(useUIStore.getState().fieldDrawer?.entity).toBe('dc_it')
    expect(useUIStore.getState().selectedComponent).toEqual({ type: 'Load', name: 'dc_it' })
    // The secondary link takes the section to Expert and closes the drawer.
    await user.click(screen.getByTestId('section-item-expert'))
    expect(useUIStore.getState().uiMode).toBe('expert')
    expect(useUIStore.getState().fieldDrawer).toBeNull()
    expect(useUIStore.getState().activeSection).toBe('loads')
  })

  it('moving the step moves the shared section, and a bespoke section keeps its card', async () => {
    const user = mount()
    await screen.findByTestId('hub-card-site')
    await user.click(rail('goal'))
    expect(await screen.findByTestId('hub-card-goal')).toBeTruthy()
    expect(useUIStore.getState().activeSection).toBe('scenarios')
  })
})

describe('one set of marks and one count (design §3.2)', () => {
  const item = (severity: string, extra: Record<string, unknown> = {}) => ({
    code: `c_${severity}_${Math.random()}`, severity, message: `${severity} item`, source: 'validation',
    blocking: false, acknowledged: false, target: { section: 'loads', panel: null, component: null, entity: null, field: null },
    ...extra,
  })

  it('the rail pill carries the dot and the open counts in its label; incomplete and unknown differ', async () => {
    const payload = flowPayload()
    const loads = payload.sections.find(x => x.section === 'loads')!
    loads.state = 'incomplete'
    loads.items = [item('error'), item('error'), item('warning'), item('info'),
      item('error', { acknowledged: true }), item('error', { in_scope: false })] as never
    vi.mocked(flowApi.getProjectFlow).mockResolvedValue(payload)
    mount()
    await screen.findByTestId('hub-card-site')
    const pill = await vi.waitFor(() => {
      const b = screen.getByTestId('hub-rail-section-loads')
      expect(b.getAttribute('aria-label')).toBe('Loads & demand: 2 to do · 1 to check')
      return b
    })
    expect(pill.querySelector('[data-state="incomplete"]')).toBeTruthy()
    // Sections 9 and 10 stay without a dot.
    expect(screen.getByTestId('hub-rail-step-results').querySelector('[data-state]')).toBeNull()
  })

  it('a flow that cannot be read shows "?" in every pill with a state, never a tick', async () => {
    vi.mocked(flowApi.getProjectFlow).mockRejectedValue(new Error('x'))
    mount()
    await screen.findByTestId('hub-card-site')
    await vi.waitFor(() => expect(screen.getByTestId('hub-rail-section-loads')
      .querySelector('[data-state="unknown"]')).toBeTruthy())
    expect(screen.getByTestId('hub-rail-section-loads').getAttribute('aria-label')).toBe('Loads & demand: not known yet')
  })

  it('the section list counts only open items and keeps info as a muted line', async () => {
    const payload = flowPayload()
    const loads = payload.sections.find(x => x.section === 'loads')!
    loads.items = [item('error'), item('info'), item('error', { in_scope: false })] as never
    vi.mocked(flowApi.getProjectFlow).mockResolvedValue(payload)
    const user = mount()
    await screen.findByTestId('hub-card-site')
    await user.click(screen.getByTestId('hub-rail-section-loads'))
    expect(await screen.findAllByTestId('section-item')).toHaveLength(1)
    expect(screen.getByTestId('section-infos').textContent).toContain('info item')
  })
})

describe('the readiness card (section 9)', () => {
  const target = { section: 'connection', panel: null, component: 'Link', entity: 'grid_import', field: null }
  async function openReadiness(over: Record<string, unknown>) {
    vi.mocked(flowApi.getProjectFlow).mockResolvedValue({ ...flowPayload(), ...over } as never)
    const user = mount()
    await screen.findByTestId('hub-card-site')
    await user.click(screen.getByTestId('hub-rail-section-readiness'))
    return user
  }

  it('says it is checking while the flow is deferred, never "Ready to run"', async () => {
    await openReadiness({ deferred: true, run_ready: false })
    expect(await screen.findByTestId('readiness-checking')).toBeTruthy()
    expect(screen.queryByTestId('readiness-ready')).toBeNull()
  })

  it('a blocker opens its field here first, Expert second', async () => {
    const blocker = { section: 'connection', message: 'Connection & grid: no import connection', target }
    const user = await openReadiness({
      run_ready: false, run_blocker: blocker,
      verdicts: { optimisation: { ready: false, blocker }, study: null },
    })
    expect((await screen.findByTestId('readiness-blocked')).textContent).toContain('no import connection')
    await user.click(screen.getByTestId('readiness-open'))
    expect(useUIStore.getState().uiMode).toBe('guided')
    expect(useUIStore.getState().fieldDrawer?.entity).toBe('grid_import')
    // The secondary link takes the same field to Expert.
    useUIStore.setState({ fieldDrawer: null, activeSection: 'readiness' })
    await user.click(await screen.findByTestId('readiness-open-expert'))
    expect(useUIStore.getState().uiMode).toBe('expert')
  })
})
