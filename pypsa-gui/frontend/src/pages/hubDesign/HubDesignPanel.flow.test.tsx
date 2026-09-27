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

describe('tour anchors', () => {
  it('renders the rail and the guide button', async () => {
    mount()
    await screen.findByTestId('hub-card-site')
    expect(screen.getByTestId('hub-rail')).toBeTruthy()
    expect(screen.getByTestId('hub-guide-button')).toBeTruthy()
    expect(screen.getByTestId('hub-design-panel')).toBeTruthy()
  })
})
