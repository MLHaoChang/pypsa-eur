// Guided-mode spec §5.3 (Goal): the allowed shortfall is the project's one
// stored reliability goal (W3b-R) — stored, else the template's, else the
// site type's default as the placeholder; "Save goal" sends only the edited
// fields. VOLL ≤ 0 offers the assistant fix. The Run is in section 9 (`shared/GoalStudyRun.test.tsx`).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { resultsApi, simulationApi } from '../../../api/simulation'
import { useChatStore } from '../../../store/chatStore'
import { useUIStore } from '../../../store/uiStore'
import { HUB_DESIGN_INITIAL, useHubDesignStore } from '../hubDesignStore'
import { VOLL_TEXT } from '../delegate'
import { DC_TEMPLATE, readiness } from '../testFixtures'
import { goal } from '../../../test/fixtures/reliabilityGoal'
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
    simulationApi: { ...actual.simulationApi, getSolverConfig: vi.fn(), updateSolverConfig: vi.fn() },
  }
})

// The scope (W4a): unread by default, so every block shows; one test reads
// a scope with Reliability off.
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
  useChatStore.setState({ composerSeed: null, requestQueue: [], lastRequest: null })
  useHubDesignStore.setState({ ...HUB_DESIGN_INITIAL, project: 'Demo', ready: true, step: 'goal' })
  getReliabilityGoal.mockResolvedValue(goal({ site_archetype: ['weak_flexible', 'template'] }))
  vi.mocked(simulationApi.updateSolverConfig).mockResolvedValue({} as never)
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

describe('GoalCard goal (W3b-R: the one stored goal)', () => {
  it('a template value is the goal, named as the template\'s', async () => {
    getReliabilityGoal.mockResolvedValue(goal({ target_lole_h: [1.5, 'template'],
      site_archetype: ['weak_flexible', 'template'] }))
    mount()
    await waitFor(() => expect(input('hub-goal-lole').value).toBe('1.5'))
    expect(screen.getByTestId('hub-goal-lole-template').textContent).toBe('From the project template')
  })

  it('a stored value wins and carries no template note', async () => {
    getReliabilityGoal.mockResolvedValue(goal({ target_lole_h: [4, 'stored'] }))
    mount()
    await waitFor(() => expect(input('hub-goal-lole').value).toBe('4'))
    expect(screen.queryByTestId('hub-goal-lole-template')).toBeNull()
  })

  it('without a saved or template goal it reads "not set", as Expert does (no pack default shown)', async () => {
    mount()
    await waitFor(() => expect(input('hub-goal-lole').disabled).toBe(false))
    expect(input('hub-goal-lole').value).toBe('')
    // readiness's pack default (3 h/yr on the data center) is never shown as the goal
    expect(input('hub-goal-lole').placeholder).toBe('not set')
    // P26 gate friction 5: the empty box says what to type, in plain words.
    expect(screen.getByTestId('hub-goal-empty-hint').textContent).toBe(
      'No goal yet. Type how many hours per year without power you can accept (for example 3) '
      + 'to get a pass or fail answer.')
    await userEvent.click(screen.getByTestId('hub-goal-advanced'))
    expect(input('hub-goal-ens').placeholder).toBe('off')
    expect(input('hub-goal-draws').placeholder).toBe('500 (default)')
  })

  it('readiness asks for the stored site type', async () => {
    getReliabilityGoal.mockResolvedValue(goal({ site_archetype: ['off_grid', 'stored'] }))
    mount()
    await waitFor(() => expect(resultsApi.getEhReadiness).toHaveBeenCalled())
    expect(vi.mocked(resultsApi.getEhReadiness).mock.calls.every(c => c[0] === 'off_grid')).toBe(true)
  })

  it('"Save goal" sends only the field the user typed', async () => {
    getReliabilityGoal.mockResolvedValue(goal({ target_lole_h: [1.5, 'template'],
      ens_cap_permyriad: [4, 'template'] }))
    const user = mount()
    await waitFor(() => expect(input('hub-goal-lole').value).toBe('1.5'))
    const save = screen.getByTestId('hub-goal-save') as HTMLButtonElement
    expect(save.disabled).toBe(true)
    await user.clear(input('hub-goal-lole'))
    await user.type(input('hub-goal-lole'), '7')
    expect(save.disabled).toBe(false)
    await user.click(save)
    await waitFor(() => expect(simulationApi.updateSolverConfig).toHaveBeenCalledTimes(1))
    expect(vi.mocked(simulationApi.updateSolverConfig).mock.calls[0][0]).toEqual({ target_lole_h: 7 })
    // the goal is read again, so both modes show the saved value
    await waitFor(() => expect(getReliabilityGoal.mock.calls.length).toBeGreaterThanOrEqual(2))
  })

  it('an invalid value is named and cannot be saved', async () => {
    const user = mount()
    await waitFor(() => expect(input('hub-goal-lole').disabled).toBe(false))
    await user.type(input('hub-goal-lole'), '-1')
    expect(screen.getByTestId('hub-goal-error').textContent).toMatch(/Allowed shortfall/)
    expect((screen.getByTestId('hub-goal-save') as HTMLButtonElement).disabled).toBe(true)
  })

  it('energy strictness and simulation runs sit behind the advanced toggle', async () => {
    const user = mount()
    expect(screen.queryByTestId('hub-goal-ens')).toBeNull()
    expect(screen.queryByTestId('hub-goal-draws')).toBeNull()
    await user.click(screen.getByTestId('hub-goal-advanced'))
    await waitFor(() => expect(input('hub-goal-ens').disabled).toBe(false))
    expect(input('hub-goal-draws').placeholder).toBe('500 (default)')
    await user.type(input('hub-goal-ens'), '2')
    await user.click(screen.getByTestId('hub-goal-save'))
    await waitFor(() => expect(simulationApi.updateSolverConfig).toHaveBeenCalledWith({ ens_cap_permyriad: 2 }))
    // A save says it landed; the next edit clears the note.
    expect((await screen.findByTestId('hub-goal-saved')).textContent).toBe('Saved.')
    await user.type(input('hub-goal-ens'), '3')
    expect(screen.queryByTestId('hub-goal-saved')).toBeNull()
  })

  it('takes a "set the goal" request from a Results study (Guided home of the goal)', async () => {
    useUIStore.setState({ solverBlockRequest: 'goal' })
    mount()
    await waitFor(() => expect(useUIStore.getState().solverBlockRequest).toBeNull())
  })
})

describe('GoalCard voll line', () => {
  it('VOLL ≤ 0 offers the assistant fix', async () => {
    vi.mocked(simulationApi.getSolverConfig).mockResolvedValue({ voll: 0 } as never)
    const user = mount()
    await user.click(await screen.findByTestId('hub-goal-voll-fix'))
    // P25 (§5.7): the fix is sent (queued for ChatPanel), not seeded.
    expect(useChatStore.getState().requestQueue.map(r => r.text)).toEqual([VOLL_TEXT])
    expect(useChatStore.getState().composerSeed).toBeNull()
    expect(useUIStore.getState().assistantDockOpen).toBe(true)
  })

  it('has no Run of its own: the study runs from Readiness and run', async () => {
    mount()
    await screen.findByTestId('hub-goal-run-hint')
    expect(screen.queryByTestId('hub-goal-run')).toBeNull()
  })

  it('keeps a set VOLL under More settings, not loose on the card (UX review L3)', async () => {
    mount()
    // while the price is still loading the line shows "…"; once read it folds away
    await waitFor(() => expect(screen.queryByTestId('hub-goal-voll')).toBeNull())
    await userEvent.click(screen.getByTestId('hub-goal-advanced'))
    expect(screen.getByTestId('hub-goal-voll')).toBeTruthy()
  })

  it('mounts the stress-conditions editor under More settings (one editor, same as Solver settings)', async () => {
    mount()
    expect(screen.queryByTestId('hub-goal-stress')).toBeNull()
    await userEvent.click(await screen.findByTestId('hub-goal-advanced'))
    expect(screen.getByTestId('hub-goal-stress').textContent).toContain('Stress conditions')
    expect(await screen.findByTestId('stress-editor')).toBeTruthy()
  })

  it('out of Reliability › Stress, the stress editor gives way to adding it in place (W4a)', async () => {
    getFlowScope.mockResolvedValue({
      scope: { version: 1, groups: ['capacity', 'reliability', 'handoff'], source: 'project', preset_id: 'eh_datacenter',
        options: { capacity: ['sizing', 'dispatch'], grid: [], control: [],
          reliability: ['goal_study'], commercial: [], handoff: ['reports'] } },
      cards: [{ id: 'reliability', label: 'Reliability', locked_reason: null, intended: ['goal_study', 'stress'],
        sections: ['assets', 'scenarios'], chip: 'Reliability',
        options: [{ id: 'goal_study', label: 'Reliability goal & study', status: 'available', reason: null },
          { id: 'stress', label: 'Stress conditions & FMEA', status: 'available', reason: null }] }],
      preset_id: 'eh_datacenter', preset_label: 'Data Center Energy Hub',
      preset: { version: 1, groups: [], options: {} },
    })
    mount()
    await userEvent.click(await screen.findByTestId('hub-goal-advanced'))
    expect((await screen.findByTestId('hub-goal-stress-scope-add')).textContent).toBe('Add Stress conditions & FMEA')
    expect(screen.queryByTestId('stress-editor')).toBeNull()
  })

  it('opens More settings with the stress editor when a link asks for it, once', async () => {
    useHubDesignStore.setState({ goalAdvancedRequested: true })
    mount()
    expect(await screen.findByTestId('stress-editor')).toBeTruthy()
    expect(useHubDesignStore.getState().goalAdvancedRequested).toBe(false)
  })

  it('shows VOLL read-only when it is set', async () => {
    mount()
    await userEvent.click(await screen.findByTestId('hub-goal-advanced'))
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

describe('GoalCard scope (W4a)', () => {
  it('with Reliability off the goal hides and VOLL stays', async () => {
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
    vi.mocked(simulationApi.getSolverConfig).mockResolvedValue({ voll: 0 } as never)
    mount()
    expect((await screen.findByTestId('hub-goal-out-of-scope-add')).textContent)
      .toBe('Add Reliability goal & study')
    expect(screen.queryByTestId('hub-goal-lole')).toBeNull()
    expect(await screen.findByTestId('hub-goal-voll')).toBeTruthy()
    expect(screen.queryByTestId('hub-goal-run')).toBeNull()
  })
})
