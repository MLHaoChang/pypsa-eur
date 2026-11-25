// DC review 02, UX GO (5): the CO₂ limit's refusal shows the solve's own
// sentence and the way to the limit's field, in the user's mode.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { resultsApi, type Co2LimitResult } from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import { CHANGE_LIMIT, Co2LimitRefusal, RAISE_LIMIT } from './Co2LimitRefusal'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return { ...actual, resultsApi: { ...actual.resultsApi, getCo2Limit: vi.fn() } }
})

const UNREACHABLE = 'CO₂ limit: no design meets 100 t CO₂/yr. The least reachable is 420 t CO₂/yr. One extra solve without the limit found it.'
const refused = (over: Partial<NonNullable<Co2LimitResult['refusal']>> = {}): Co2LimitResult => ({
  limit_t_per_yr: 100,
  refusal: { code: 'co2_cap_unreachable', message: UNREACHABLE, least_t_per_yr: { all: 420 },
    without_limit: 'feasible', extra_solves: 1, ...over },
})

function mount(mode: 'guided' | 'expert') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><Co2LimitRefusal mode={mode} /></QueryClientProvider>)
  return userEvent.setup()
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo', uiMode: 'guided', activeSlidePanel: 'results', flowReturn: null,
    solverBlockRequest: null })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('Co2LimitRefusal', () => {
  it('says nothing without a limit, or when the limit was met', async () => {
    vi.mocked(resultsApi.getCo2Limit).mockResolvedValue(null)
    mount('guided')
    await vi.waitFor(() => expect(resultsApi.getCo2Limit).toHaveBeenCalled())
    expect(screen.queryByTestId('hub-co2-refusal')).toBeNull()
    cleanup()
    vi.mocked(resultsApi.getCo2Limit).mockResolvedValue({ limit_t_per_yr: 100, refusal: null })
    mount('guided')
    await vi.waitFor(() => expect(resultsApi.getCo2Limit).toHaveBeenCalledTimes(2))
    expect(screen.queryByTestId('hub-co2-refusal')).toBeNull()
  })

  it('Guided: the solve\'s sentence and "Raise the limit in the Goal step ›" to the Goal card, with Back', async () => {
    vi.mocked(resultsApi.getCo2Limit).mockResolvedValue(refused())
    const user = mount('guided')
    const box = await screen.findByTestId('hub-co2-refusal')
    expect(box.textContent).toContain(UNREACHABLE)
    await user.click(screen.getByRole('button', { name: RAISE_LIMIT.guided }))
    const ui = useUIStore.getState()
    expect(ui.solverBlockRequest).toBe('goal')
    expect(ui.activeSlidePanel).toBe('hubDesign')
    expect(ui.uiMode).toBe('guided')
    expect(ui.flowReturn).not.toBeNull()
  })

  it('Expert: the link names Solver settings and keeps the mode', async () => {
    useUIStore.setState({ uiMode: 'expert' })
    vi.mocked(resultsApi.getCo2Limit).mockResolvedValue(refused())
    const user = mount('expert')
    await screen.findByTestId('results-co2-refusal')
    await user.click(screen.getByRole('button', { name: RAISE_LIMIT.expert }))
    expect(useUIStore.getState().uiMode).toBe('expert')
    expect(useUIStore.getState().solverBlockRequest).toBe('goal')
  })

  it('no link when no design works without the limit either; "Change" for a limit that could not be read', async () => {
    vi.mocked(resultsApi.getCo2Limit).mockResolvedValue(refused({ without_limit: 'infeasible',
      message: 'CO₂ limit: no design meets 100 t CO₂/yr. One extra solve without the limit found no design either, so the limit is not the cause.' }))
    mount('guided')
    await screen.findByTestId('hub-co2-refusal')
    expect(screen.queryByRole('button')).toBeNull()
    cleanup()
    vi.mocked(resultsApi.getCo2Limit).mockResolvedValue({ limit_t_per_yr: null, rows: [],
      refusal: { code: 'co2_cap_invalid', message: 'CO₂ limit: "abc" is not a number of t CO₂/yr.' } } as never)
    mount('guided')
    expect(await screen.findByRole('button', { name: CHANGE_LIMIT.guided })).toBeTruthy()
  })
})
