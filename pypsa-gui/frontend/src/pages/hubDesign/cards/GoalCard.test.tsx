// Guided-mode spec §5.3 (Goal): the allowed shortfall defaults from the
// template, then from the pack readiness reports; VOLL ≤ 0 offers the
// assistant fix. The Run is in section 9 (`shared/GoalStudyRun.test.tsx`).
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

  it('has no Run of its own: the study runs from Readiness → run', async () => {
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
