// Template-e2e issue 22, S2b: before the study has results, Guided's Investment
// tile is not headlined and runs the investment case itself, with the same panel
// and words as Expert; a headlined tile leaves that to the headline's NPV figure.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { financeApi } from '../../api/finance'
import { campusApi } from '../../api/campusElectrical'
import { getFlowScope } from '../../api/flow'
import { simulationApi } from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import { HUB_DESIGN_INITIAL, useHubDesignStore } from './hubDesignStore'
import { ChaptersSection } from './ChaptersSection'
import type { ChapterId, ChapterInputs } from './chapters'
import { COMPARES } from '../results/investment/investmentCaseNeeds'

vi.mock('../../api/finance', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/finance')>()
  return { ...actual, financeApi: { ...actual.financeApi,
    getInvestmentCase: vi.fn(), getReport: vi.fn(), startInvestmentCase: vi.fn() } }
})
vi.mock('../../api/flow', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/flow')>()), getFlowScope: vi.fn(),
}))
vi.mock('../../api/campusElectrical', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/campusElectrical')>()
  return { ...actual, campusApi: { ...actual.campusApi, state: vi.fn(), gfmSettings: vi.fn() } }
})
vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return { ...actual, simulationApi: { ...actual.simulationApi, getStatus: vi.fn() } }
})

const RELIABILITY: ChapterInputs['reliability'] =
  { hasResults: false, running: false, lole: null, target: null, verdict: null }

function mount(headlined: ChapterId[] = []) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}>
    <ChaptersSection reliability={RELIABILITY} headlined={headlined} /></QueryClientProvider>)
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo', activeSlidePanel: 'hubDesign', resultsTabRequest: null })
  useHubDesignStore.setState({ ...HUB_DESIGN_INITIAL, project: 'Demo', ready: true, step: 'results' })
  vi.mocked(financeApi.getInvestmentCase).mockResolvedValue(null)
  vi.mocked(financeApi.getReport).mockResolvedValue(null)
  vi.mocked(getFlowScope).mockRejectedValue(new Error('no scope'))
  vi.mocked(campusApi.state).mockRejectedValue(Object.assign(new Error('409'), { response: { status: 409 } }))
  vi.mocked(simulationApi.getStatus).mockResolvedValue(
    { running: false, status: 'idle', condition: null, objective: null, solve_time: null, dispatch: 'none' })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('the Investment tile before the study has results', () => {
  it('says what it compares once, and runs from the tile', async () => {
    vi.mocked(financeApi.startInvestmentCase).mockResolvedValue({ status: 'running' } as never)
    mount()
    expect((await screen.findByTestId('ic-not-run')).textContent).toBe(COMPARES)
    // The pill says "Not run yet"; the panel's line does not say it again (UX 2 on #1886).
    expect(screen.getByTestId('hub-chapter-investment').textContent?.split('Not run yet').length).toBe(2)
    expect(screen.getByTestId('hub-chapter-investment').textContent?.split('investment case has not run').length).toBe(1)
    fireEvent.click(screen.getByRole('button', { name: 'Run the investment case' }))
    await waitFor(() => expect(financeApi.startInvestmentCase).toHaveBeenCalledTimes(1))
  })

  it('a stored refusal: the checklist with its link; the tile repeats neither the need nor the code', async () => {
    vi.mocked(financeApi.getInvestmentCase).mockResolvedValue(
      { status: 'refused', error_code: 'not_solved', error: 'not_solved: solve first' } as never)
    mount()
    expect((await screen.findByTestId('ic-need-solve')).textContent).toBe('A solved project — Run the solve')
    expect(screen.getByTestId('hub-chapter-investment').textContent).not.toMatch(/not_solved|needs: a solved/)
  })

  it('a headlined tile carries no run panel (one entry point per destination)', async () => {
    mount(['investment'])
    await screen.findByTestId('hub-chapter-investment-headlined')
    expect(screen.queryByTestId('ic-run-panel')).toBeNull()
  })
})
