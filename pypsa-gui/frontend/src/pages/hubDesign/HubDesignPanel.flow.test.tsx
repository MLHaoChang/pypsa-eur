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
import { guidesApi } from '../../components/GuidedTour'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import { HUB_DESIGN_INITIAL, useHubDesignStore } from './hubDesignStore'
import { BLOCKED_NO_PROJECT, BLOCKED_NO_STUDY } from './flow'
import { DC_TEMPLATE, REPORT, readiness, review } from './testFixtures'
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

const RUNNING = { status: 'running', archetype: 'weak_flexible' as const }
const DONE = { status: 'done', archetype: 'weak_flexible' as const, report: REPORT }

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
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

  it('no_study on a template project: Start ✓, opens on Site; Results / Improve blocked', async () => {
    mount()
    await screen.findByTestId('hub-card-site')
    expect(railStates()).toEqual({ start: 'done', site: 'current', goal: 'todo',
      results: 'blocked', improve: 'blocked' })
    expect(rail('results').getAttribute('title')).toBe(BLOCKED_NO_STUDY)
  })

  it('no_study on an own network: opens on Start (not ✓)', async () => {
    vi.mocked(resultsApi.getEhTemplate).mockResolvedValue(null)
    mount()
    await screen.findByTestId('hub-card-start')
    expect(railStates()).toMatchObject({ start: 'current', site: 'todo', results: 'blocked' })
  })

  it('failed without a report is no_study; Goal shows the error and Run again', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({ status: 'failed', error: 'boom', report: null })
    // the route answers 204 for a failed study (N2): the review is not asked
    const user = mount()
    await screen.findByTestId('hub-card-site')
    expect(railStates()).toMatchObject({ results: 'blocked', improve: 'blocked' })
    await user.click(rail('goal'))
    expect((await screen.findByTestId('hub-goal-error')).textContent).toContain('boom')
    expect(screen.getByTestId('hub-goal-run').textContent).toBe('Run again')
    expect(resultsApi.getEhReview).not.toHaveBeenCalled()
  })

  it('running: opens on Goal with a spinner; Results / Improve blocked', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(RUNNING)
    mount()
    await screen.findByTestId('hub-card-goal')
    expect(railStates()).toEqual({ start: 'done', site: 'done', goal: 'current',
      results: 'blocked', improve: 'blocked' })
    expect(screen.getByTestId('hub-rail-spinner')).toBeTruthy()
    expect(screen.getByTestId('hub-goal-running')).toBeTruthy()
  })

  it('done: opens on Results; every step enabled', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(DONE)
    vi.mocked(resultsApi.getEhReview).mockResolvedValue(review())
    mount()
    await screen.findByTestId('hub-card-results')
    expect(railStates()).toEqual({ start: 'done', site: 'done', goal: 'done',
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
    await screen.findByTestId('hub-card-goal')
    setStudy(DONE)
    await screen.findByTestId('hub-card-results')
    expect(useHubDesignStore.getState().step).toBe('results')
  })

  it('a manual rail click suppresses it, once', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(RUNNING)
    vi.mocked(resultsApi.getEhReview).mockResolvedValue(review())
    const user = mount()
    await screen.findByTestId('hub-card-goal')
    await user.click(rail('site'))
    expect(useHubDesignStore.getState().userMovedRail).toBe(true)
    setStudy(DONE)
    await waitFor(() => expect(useHubDesignStore.getState().userMovedRail).toBe(false))
    expect(useHubDesignStore.getState().step).toBe('site')
    expect(screen.getByTestId('hub-card-site')).toBeTruthy()
  })

  it('done → running moves the rail to Goal', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(DONE)
    vi.mocked(resultsApi.getEhReview).mockResolvedValue(review())
    const user = mount()
    await screen.findByTestId('hub-card-results')
    await user.click(rail('improve'))
    setStudy(RUNNING)
    await screen.findByTestId('hub-card-goal')
    expect(useHubDesignStore.getState().step).toBe('goal')
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
    expect(useHubDesignStore.getState()).toMatchObject({ project: 'Other', step: 'start',
      archetype: 'strong_grid', loleTarget: '' })
  })

  it('a template project takes its archetype and override from the template', async () => {
    vi.mocked(resultsApi.getEhTemplate).mockResolvedValue(
      { ...DC_TEMPLATE, pack_overrides: { import_p_nom_mw: 40, target_lole_h: 2, ens_cap_permyriad: 4 } })
    mount()
    await screen.findByTestId('hub-card-site')
    expect(useHubDesignStore.getState()).toMatchObject({ archetype: 'weak_flexible',
      loleTarget: '2', loleSource: 'template', ensCap: '4' })
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
      'hub-site-type', 'hub-goal-lole', 'hub-goal-run'])
    expect(screen.queryByTestId('guide-tour')).toBeNull()
    // the tour's reveal clicks did not count as the user moving the rail
    expect(useHubDesignStore.getState().userMovedRail).toBe(false)
    setStudy(RUNNING)
    await screen.findByTestId('hub-card-goal')
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
  it('template 500 → the line names the template, hub-goal-run disabled', async () => {
    vi.mocked(resultsApi.getEhTemplate).mockRejectedValue(boom())
    const user = mount()
    const line = await screen.findByTestId('hub-load-error')
    expect(line.textContent).toMatch(
      /^This project's template could not be read from the server, so the steps below may be incomplete\./)
    await user.click(rail('goal'))
    const run = await screen.findByTestId('hub-goal-run') as HTMLButtonElement
    expect(run.disabled).toBe(true)
    expect(run.title).toBe('The template could not be read — retry above.')
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
