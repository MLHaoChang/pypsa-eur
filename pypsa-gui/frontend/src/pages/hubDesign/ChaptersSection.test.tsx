// The Guided report entry's tiles from the four reads (UX S6, G4/G6).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { financeApi } from '../../api/finance'
import { campusApi } from '../../api/campusElectrical'
import { simulationApi } from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import { HUB_DESIGN_INITIAL, useHubDesignStore } from './hubDesignStore'
import { ChaptersSection } from './ChaptersSection'
import type { ChapterInputs } from './chapters'

vi.mock('../../api/finance', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/finance')>()
  return { ...actual, financeApi: { ...actual.financeApi, getInvestmentCase: vi.fn(), getReport: vi.fn() } }
})
vi.mock('../../api/campusElectrical', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/campusElectrical')>()
  return { ...actual, campusApi: { ...actual.campusApi, state: vi.fn(), gfmSettings: vi.fn() } }
})
vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return { ...actual, simulationApi: { ...actual.simulationApi, getStatus: vi.fn() } }
})

const RELIABILITY: ChapterInputs['reliability'] =
  { hasResults: true, running: false, lole: 12.38, target: 3, verdict: 'fail' }

function mount(reliability: ChapterInputs['reliability'] = RELIABILITY) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><ChaptersSection reliability={reliability} /></QueryClientProvider>)
  return userEvent.setup()
}
const kpi = (id: string) => screen.getByTestId(`hub-chapter-${id}-kpi`)

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo', activeSlidePanel: 'hubDesign', resultsTabRequest: null })
  useHubDesignStore.setState({ ...HUB_DESIGN_INITIAL, project: 'Demo', ready: true, step: 'results' })
  vi.mocked(financeApi.getInvestmentCase).mockResolvedValue({ status: 'done' } as never)
  vi.mocked(financeApi.getReport).mockResolvedValue({ npv_at_wacc: null,
    sections: { project: { status: 'ok', payload: { project_pre_tax_npv: -11_010_262 } } } } as never)
  vi.mocked(campusApi.state).mockRejectedValue(Object.assign(new Error('409'), { response: { status: 409 } }))
  vi.mocked(simulationApi.getStatus).mockResolvedValue(
    { running: false, status: 'idle', condition: null, objective: null, solve_time: null, dispatch: 'none' })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('ChaptersSection', () => {
  it('four tiles in the category order, each a figure or a state in words', async () => {
    mount()
    await waitFor(() => expect(kpi('investment').getAttribute('data-state')).toBe('ready'))
    // The result groups' own names, in their order.
    expect(screen.getAllByTestId(/^hub-chapter-[a-z_]+$/).map(e => e.firstElementChild?.textContent))
      .toEqual(['Investment', 'Grid plan', 'Reliability', 'Operation'])
    expect(kpi('investment').textContent).toMatch(/before tax/)
    await waitFor(() => expect(kpi('grid_plan').getAttribute('data-state')).toBe('not_applicable'))
    expect(kpi('reliability').getAttribute('data-kpi-value') ?? kpi('reliability').textContent).toMatch(/12\.4/)
    await waitFor(() => expect(kpi('operation').getAttribute('data-state')).toBe('not_run'))
    expect(kpi('operation').textContent).toMatch(/reliability study does not solve it/)
  })

  it('a tile opens its own detail', async () => {
    const user = mount()
    await user.click(await screen.findByTestId('hub-chapter-investment-open'))
    expect(useUIStore.getState().activeSlidePanel).toBe('results')
    expect(useUIStore.getState().resultsTabRequest).toBe('investment')
    await user.click(screen.getByTestId('hub-chapter-grid_plan-open'))
    expect(useUIStore.getState().activeSlidePanel).toBe('campusElectrical')
  })

  it('no study yet: the reliability tile goes to the Goal step', async () => {
    const user = mount({ hasResults: false, running: false, lole: null, target: null, verdict: null })
    expect(kpi('reliability').getAttribute('data-state')).toBe('not_run')
    await user.click(screen.getByTestId('hub-chapter-reliability-open'))
    expect(useHubDesignStore.getState().step).toBe('goal')
  })

  it('the operation tile says an applied GFM setting needs a re-solve, with Solve (GFM-b)', async () => {
    const requestRunSolve = vi.fn()
    useUIStore.setState({ requestRunSolve })
    vi.mocked(campusApi.state).mockResolvedValue({ results: null, stale: false } as never)
    vi.mocked(campusApi.gfmSettings).mockResolvedValue({ state: 'not_run', reason: 'x', recommendation: null,
      applied: { at: 1, changes: [], hub_resolve_needed: true } } as never)
    const user = mount()
    const line = await screen.findByTestId('hub-chapter-operation-control')
    expect(line.textContent).toMatch(/Re-solve the hub: these settings change its island rows/)
    await user.click(screen.getByTestId('hub-chapter-operation-solve'))
    expect(requestRunSolve).toHaveBeenCalled()
  })

  it('no control line when the project has no campus', async () => {
    mount()
    await waitFor(() => expect(kpi('grid_plan').getAttribute('data-state')).toBe('not_applicable'))
    expect(screen.queryByTestId('hub-chapter-operation-control')).toBeNull()
    expect(campusApi.gfmSettings).not.toHaveBeenCalled()
  })
})

