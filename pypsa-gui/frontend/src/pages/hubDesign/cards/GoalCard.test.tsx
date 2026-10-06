// Guided-mode spec §5.3 (Goal): the allowed shortfall defaults from the
// template, then from the pack readiness reports; Run posts exactly what the
// Expert panel's `buildEhStudyBody` builds for the same inputs; VOLL ≤ 0
// blocks the run and offers the assistant fix; running / failed / aborted are
// read from the study record (P24-BE gate N2).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { resultsApi, simulationApi } from '../../../api/simulation'
import { useChatStore } from '../../../store/chatStore'
import { useUIStore } from '../../../store/uiStore'
import { buildEhStudyBody, EMPTY_PACK_FORM, formFromTemplate } from '../../results/EhReferenceDesignPanel'
import { HUB_DESIGN_INITIAL, useHubDesignStore } from '../hubDesignStore'
import { VOLL_TEXT } from '../delegate'
import { DC_TEMPLATE, readiness } from '../testFixtures'
import { GoalCard } from './GoalCard'
import { nk } from '../../../utils/queryKeys'

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

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo', assistantDockOpen: false })
  useChatStore.setState({ composerSeed: null, requestQueue: [], lastRequest: null })
  useHubDesignStore.setState({ ...HUB_DESIGN_INITIAL, project: 'Demo', ready: true,
    step: 'goal', archetype: 'weak_flexible' })
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
  render(<QueryClientProvider client={client}><GoalCard /></QueryClientProvider>)
  return userEvent.setup()
}
const input = (id: string) => screen.getByTestId(id) as HTMLInputElement

describe('GoalCard defaults', () => {
  it('a template override wins over the pack default', async () => {
    useHubDesignStore.setState({ loleTarget: '1.5', loleSource: 'template' })
    mount()
    await waitFor(() => expect(resultsApi.getEhReadiness).toHaveBeenCalled())
    await new Promise(r => setTimeout(r, 0))
    expect(input('hub-goal-lole').value).toBe('1.5')
  })

  it('without one, the pack default from readiness (3 h/yr on the data center)', async () => {
    mount()
    await waitFor(() => expect(input('hub-goal-lole').value).toBe('3'))
    expect(useHubDesignStore.getState().loleSource).toBe('pack')
  })

  it('a pack without a target leaves the goal empty', async () => {
    vi.mocked(resultsApi.getEhReadiness).mockResolvedValue(readiness({
      pack_defaults: { target_lole_h: null, ens_cap_permyriad: 10, certification_metric: 'none' } }))
    mount()
    await waitFor(() => expect(resultsApi.getEhReadiness).toHaveBeenCalled())
    await new Promise(r => setTimeout(r, 0))
    expect(input('hub-goal-lole').value).toBe('')
    // P26 gate friction 5: the empty box says what to type, in plain words.
    expect(input('hub-goal-lole').placeholder).toBe('e.g. 3')
    expect(screen.getByTestId('hub-goal-empty-hint').textContent).toBe(
      'No goal yet. Type how many hours per year without power you can accept (for example 3) '
      + 'to get a pass or fail answer.')
  })

  // The pack default depends on the site type: strong_grid has none here.
  const byType = (a: string) => readiness({ pack_defaults: a === 'strong_grid'
    ? { target_lole_h: null, ens_cap_permyriad: 10, certification_metric: 'none' }
    : { target_lole_h: 3, ens_cap_permyriad: 10, certification_metric: 'mc_lole' } })

  it('a pack-seeded goal follows the pack when the site type changes', async () => {
    vi.mocked(resultsApi.getEhReadiness).mockImplementation(async a => byType(a))
    mount()
    await waitFor(() => expect(input('hub-goal-lole').value).toBe('3'))
    act(() => useHubDesignStore.getState().setArchetype('strong_grid'))
    await waitFor(() => expect(input('hub-goal-lole').value).toBe(''))
  })

  it('the user\'s own value is never overwritten by the pack', async () => {
    vi.mocked(resultsApi.getEhReadiness).mockImplementation(async a => byType(a))
    const user = mount()
    await waitFor(() => expect(input('hub-goal-lole').value).toBe('3'))
    await user.clear(input('hub-goal-lole'))
    await user.type(input('hub-goal-lole'), '7')
    expect(useHubDesignStore.getState()).toMatchObject({ loleTarget: '7', loleSource: 'user' })
    act(() => useHubDesignStore.getState().setArchetype('strong_grid'))
    await waitFor(() => expect(resultsApi.getEhReadiness).toHaveBeenCalledWith(
      'strong_grid', undefined, undefined, expect.anything()))
    await new Promise(r => setTimeout(r, 20))
    expect(input('hub-goal-lole').value).toBe('7')
  })

  it('energy strictness sits behind the advanced toggle', async () => {
    const user = mount()
    expect(screen.queryByTestId('hub-goal-ens')).toBeNull()
    await user.click(screen.getByTestId('hub-goal-advanced'))
    await user.type(input('hub-goal-ens'), '2')
    expect(useHubDesignStore.getState().ensCap).toBe('2')
  })
})

describe('GoalCard run', () => {
  it('posts the Expert panel\'s body for the same inputs', async () => {
    const user = mount()
    await waitFor(() => expect(input('hub-goal-lole').value).toBe('3'))
    await user.click(screen.getByTestId('hub-goal-advanced'))
    await user.type(input('hub-goal-ens'), '5')
    await waitFor(() => expect((screen.getByTestId('hub-goal-run') as HTMLButtonElement).disabled).toBe(false))
    await user.click(screen.getByTestId('hub-goal-run'))
    const expected = buildEhStudyBody('weak_flexible',
      { ...formFromTemplate(DC_TEMPLATE), loleTarget: '3', ensCap: '5' }).body
    await waitFor(() => expect(resultsApi.startEhStudy).toHaveBeenCalledTimes(1))
    expect(vi.mocked(resultsApi.startEhStudy).mock.calls[0][0]).toEqual(expected)
    expect(expected?.pack_overrides).toMatchObject({ import_p_nom_mw: 40, target_lole_h: 3,
      ens_cap_permyriad: 5, certification_metric: 'mc_lole' })
  })

  it('an own network runs from the empty pack form', async () => {
    vi.mocked(resultsApi.getEhTemplate).mockResolvedValue(null)
    useHubDesignStore.setState({ archetype: 'strong_grid' })
    vi.mocked(resultsApi.getEhReadiness).mockResolvedValue(readiness({
      pack_defaults: { target_lole_h: null, ens_cap_permyriad: 10, certification_metric: 'none' } }))
    const user = mount()
    await waitFor(() => expect((screen.getByTestId('hub-goal-run') as HTMLButtonElement).disabled).toBe(false))
    await user.click(screen.getByTestId('hub-goal-run'))
    await waitFor(() => expect(resultsApi.startEhStudy).toHaveBeenCalledWith(
      buildEhStudyBody('strong_grid', EMPTY_PACK_FORM).body))
  })

  it('an invalid goal blocks the run with the builder\'s own message', async () => {
    const user = mount()
    await waitFor(() => expect(input('hub-goal-lole').value).toBe('3'))
    await user.clear(input('hub-goal-lole'))
    await user.type(input('hub-goal-lole'), '-1')
    expect(screen.getByTestId('hub-goal-blocked').textContent).toContain('LOLE target must be ≥ 0 h/yr')
    expect((screen.getByTestId('hub-goal-run') as HTMLButtonElement).disabled).toBe(true)
  })

  it('a refused start (409) shows the backend sentence', async () => {
    vi.mocked(resultsApi.startEhStudy).mockRejectedValue(Object.assign(new Error('409'),
      { response: { status: 409, data: { detail: 'an FMEA sweep is running — wait for it' } } }))
    const user = mount()
    await waitFor(() => expect((screen.getByTestId('hub-goal-run') as HTMLButtonElement).disabled).toBe(false))
    await user.click(screen.getByTestId('hub-goal-run'))
    await waitFor(() => expect(screen.getByTestId('hub-goal-blocked').textContent)
      .toContain('an FMEA sweep is running — wait for it'))
  })

  it('VOLL ≤ 0 disables Run and offers the assistant fix', async () => {
    vi.mocked(simulationApi.getSolverConfig).mockResolvedValue({ voll: 0 } as never)
    const user = mount()
    await screen.findByTestId('hub-goal-voll-fix')
    expect((screen.getByTestId('hub-goal-run') as HTMLButtonElement).disabled).toBe(true)
    await user.click(screen.getByTestId('hub-goal-voll-fix'))
    // P25 (§5.7): the fix is sent (queued for ChatPanel), not seeded.
    expect(useChatStore.getState().requestQueue.map(r => r.text)).toEqual([VOLL_TEXT])
    expect(useChatStore.getState().composerSeed).toBeNull()
    expect(useUIStore.getState().assistantDockOpen).toBe(true)
  })

  it('shows VOLL read-only when it is set', async () => {
    mount()
    await waitFor(() => expect(screen.getByTestId('hub-goal-voll').textContent).toContain('€5,000'))
    // the colon sits with its label (no flex gap before it)
    expect(screen.getByTestId('term-voll_plain').parentElement!.textContent)
      .toBe('Price of undelivered energy:')
    expect(screen.queryByTestId('hub-goal-voll-fix')).toBeNull()
    // P30 (C12): the unit has its own hover.
    const unit = screen.getByTestId('term-mwh')
    expect(unit.textContent).toContain('MWh')
    expect(unit.getAttribute('data-tip')).toMatch(/megawatt-hour/)
    expect(screen.getByTestId('hub-goal-voll').textContent).toContain('€5,000 per MWh')
  })
})

describe('GoalCard study states (from the study record, N2)', () => {
  it('running: the card\'s own running text and Abort — never the review\'s message', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({ status: 'running', budget_solves: 40 })
    vi.mocked(resultsApi.abortEhStudy).mockResolvedValue({ status: 'running', aborting: true })
    const user = mount()
    const t = await screen.findByTestId('hub-goal-running')
    expect(t.textContent).toBe('Studying… (up to 40 calculation steps)')
    expect(t.textContent).not.toMatch(/poll|get_adequacy_results|solves/)
    expect((screen.getByTestId('hub-goal-run') as HTMLButtonElement).disabled).toBe(true)
    await user.click(screen.getByTestId('hub-goal-abort'))
    expect(resultsApi.abortEhStudy).toHaveBeenCalled()
  })

  it('running text: plain words, and never "0 of N" when the count is missing', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({ status: 'running', budget_solves: 40,
      report: { archetype: 'weak_flexible', pack_hash: 'h', assumptions_hash: 'a',
        pipeline: { budget_solves: 40 } } })
    mount()
    const t = await screen.findByTestId('hub-goal-running')
    expect(t.textContent).toBe('Studying… (up to 40 calculation steps)')
  })

  it('running text with a count: "n of N calculation steps"', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({ status: 'running',
      report: { archetype: 'weak_flexible', pack_hash: 'h', assumptions_hash: 'a',
        pipeline: { budget_solves: 40, solves_consumed: 12 } } })
    mount()
    expect((await screen.findByTestId('hub-goal-running')).textContent)
      .toBe('Studying… 12 of 40 calculation steps')
  })

  it('failed: the error and Run again', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(
      { status: 'failed', error: 'solver infeasible', report: null })
    mount()
    await waitFor(() => expect(screen.getByTestId('hub-goal-error').textContent)
      .toContain('solver infeasible'))
    expect(screen.getByTestId('hub-goal-run').textContent).toBe('Run again')
  })

  it('aborted: says so and offers Run again', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({ status: 'aborted', report: null })
    mount()
    await waitFor(() => expect(screen.getByTestId('hub-goal-error').textContent).toMatch(/Stopped/))
    expect(screen.getByTestId('hub-goal-run').textContent).toBe('Run again')
  })
})

// P24-FE re-gate note 2 (P25 step 0): when only the template read failed the
// card cannot know the template's recommended settings, so Run must not
// silently start the default-site-type study. It is disabled with a plain
// reason and a Retry that re-reads the template; once the read recovers Run
// posts the template's body.
describe('GoalCard when the template read failed', () => {
  it('disables Run with a plain reason and a Retry that recovers', async () => {
    vi.mocked(resultsApi.getEhTemplate).mockRejectedValueOnce(new Error('500'))
    const user = mount()
    const reason = await screen.findByTestId('hub-goal-template-error')
    expect(reason.textContent).toMatch(/recommended settings could not be read/)
    expect(reason.textContent).not.toMatch(/eh_template|500/)
    expect((screen.getByTestId('hub-goal-run') as HTMLButtonElement).disabled).toBe(true)
    // P30 (B8): the disabled Run says why.
    expect(screen.getByTestId('hub-goal-run').getAttribute('title'))
      .toBe('The template could not be read — retry above.')
    await user.click(screen.getByTestId('hub-goal-run'))
    expect(resultsApi.startEhStudy).not.toHaveBeenCalled()

    await user.click(screen.getByTestId('hub-goal-template-retry'))
    await waitFor(() => expect(screen.queryByTestId('hub-goal-template-error')).toBeNull())
    await waitFor(() => expect((screen.getByTestId('hub-goal-run') as HTMLButtonElement)
      .disabled).toBe(false))
    expect(screen.getByTestId('hub-goal-run').getAttribute('title')).toBeNull()
    await user.click(screen.getByTestId('hub-goal-run'))
    await waitFor(() => expect(resultsApi.startEhStudy).toHaveBeenCalledTimes(1))
    const form = { ...formFromTemplate(DC_TEMPLATE),
      loleTarget: useHubDesignStore.getState().loleTarget,
      ensCap: useHubDesignStore.getState().ensCap }
    expect(vi.mocked(resultsApi.startEhStudy).mock.calls[0][0])
      .toEqual(buildEhStudyBody('weak_flexible', form).body)
  })

  it('an own network (template read → null) runs as before', async () => {
    vi.mocked(resultsApi.getEhTemplate).mockResolvedValue(null)
    mount()
    await waitFor(() => expect((screen.getByTestId('hub-goal-run') as HTMLButtonElement)
      .disabled).toBe(false))
    expect(screen.queryByTestId('hub-goal-template-error')).toBeNull()
  })
})

// A1-FE (deferred spec 2026-09-28 §2.4): the VOLL "Let the assistant set it"
// button is disabled with one plain sentence while an FMEA sweep runs.
describe('GoalCard while a risk check runs (A1-FE)', () => {
  const LIVE = 'A risk check is running — wait for it to finish or abort it before changing the network.'
  it('the VOLL fix is disabled with the sentence while the sweep runs; enabled after', async () => {
    vi.mocked(simulationApi.getSolverConfig).mockResolvedValue({ voll: 0 } as never)
    vi.mocked(resultsApi.getFmeaModes).mockResolvedValue({ per_mode: [], sweep_status: 'running' } as never)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><GoalCard /></QueryClientProvider>)
    const fix = await screen.findByTestId('hub-goal-voll-fix') as HTMLButtonElement
    await waitFor(() => expect(fix.disabled).toBe(true))
    expect(fix.title).toBe(LIVE)
    await act(async () => {
      client.setQueryData(nk('Demo', 'results', 'fmea_modes'), { per_mode: [], sweep_status: 'done' })
    })
    await waitFor(() => expect(fix.disabled).toBe(false))
  })
})
