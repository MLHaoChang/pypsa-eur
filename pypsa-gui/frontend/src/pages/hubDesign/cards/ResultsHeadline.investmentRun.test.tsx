// Template-e2e issue 22, S2b: Guided runs the investment case from the Results
// headline's NPV figure, with the same panel and words as Expert; the figure's
// reason steps aside where the panel states the line, so nothing shows twice.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { financeApi } from '../../../api/finance'
import { campusApi } from '../../../api/campusElectrical'
import { resultsApi, simulationApi, type HeadlineKpis } from '../../../api/simulation'
import { useUIStore } from '../../../store/uiStore'
import { useProjectFlow } from '../../../hooks/useProjectFlow'
import { HUB_DESIGN_INITIAL, useHubDesignStore } from '../hubDesignStore'
import { REPORT, review } from '../testFixtures'
import captured from '../../../test/fixtures/headlineKpis.json'
import { ResultsCard } from './ResultsCard'
import { BEFORE_RUN } from '../../results/investment/investmentCaseNeeds'

vi.mock('../../../hooks/useProjectFlow', () => ({ useProjectFlow: vi.fn() }))
vi.mock('../../../api/finance', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/finance')>()
  return { ...actual, financeApi: { ...actual.financeApi,
    getInvestmentCase: vi.fn(), getReport: vi.fn(), startInvestmentCase: vi.fn() } }
})
vi.mock('../../../api/campusElectrical', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/campusElectrical')>()
  return { ...actual, campusApi: { ...actual.campusApi, state: vi.fn(), gfmSettings: vi.fn() } }
})
vi.mock('../../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/simulation')>()
  return {
    ...actual,
    simulationApi: { ...actual.simulationApi, getStatus: vi.fn() },
    resultsApi: { ...actual.resultsApi, getEhStudy: vi.fn(), getEhReview: vi.fn(), getEhReferenceDesign: vi.fn(),
      getEhTemplate: vi.fn(), getHeadlineKpis: vi.fn(), getProcessView: vi.fn() },
  }
})

beforeEach(() => {
  vi.mocked(useProjectFlow).mockReturnValue({ flow: null } as unknown as ReturnType<typeof useProjectFlow>)
  useUIStore.setState({ currentProject: 'Demo', activeSlidePanel: 'hubDesign', uiMode: 'guided',
    resultsTabRequest: null, ehReportRequest: false, flowReturn: null })
  useHubDesignStore.setState({ ...HUB_DESIGN_INITIAL, project: 'Demo', ready: true, step: 'results' })
  vi.mocked(resultsApi.getEhStudy).mockResolvedValue(
    { status: 'done', archetype: 'weak_flexible', report: REPORT, edited_since_study: false } as never)
  vi.mocked(resultsApi.getEhReview).mockResolvedValue(review())
  vi.mocked(resultsApi.getEhReferenceDesign).mockResolvedValue(null)
  vi.mocked(resultsApi.getEhTemplate).mockResolvedValue(null)
  vi.mocked(resultsApi.getProcessView).mockResolvedValue(null as never)
  vi.mocked(resultsApi.getHeadlineKpis).mockResolvedValue(captured as HeadlineKpis)
  vi.mocked(financeApi.getInvestmentCase).mockResolvedValue(null)
  vi.mocked(financeApi.getReport).mockResolvedValue(null)
  vi.mocked(campusApi.state).mockRejectedValue(Object.assign(new Error('409'), { response: { status: 409 } }))
  vi.mocked(simulationApi.getStatus).mockResolvedValue(
    { running: false, status: 'idle', condition: null, objective: 1, solve_time: null, dispatch: 'optimal' } as never)
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><ResultsCard /></QueryClientProvider>)
}
const npvBlock = () => screen.getByTestId('hub-results-npv').parentElement!.parentElement!

describe('the Guided NPV figure runs the investment case', () => {
  it('before a run: says what it compares once, and runs from the headline', async () => {
    vi.mocked(financeApi.startInvestmentCase).mockResolvedValue({ status: 'running' } as never)
    mount()
    expect((await screen.findByTestId('ic-not-run')).textContent).toBe(BEFORE_RUN)
    expect(npvBlock().textContent?.split('The investment case compares').length).toBe(2)
    fireEvent.click(screen.getByRole('button', { name: 'Run the investment case' }))
    await waitFor(() => expect(financeApi.startInvestmentCase).toHaveBeenCalledTimes(1))
  })

  it('a stored refusal: the checklist with its link; the figure repeats neither the need nor the code', async () => {
    vi.mocked(financeApi.getInvestmentCase).mockResolvedValue(
      { status: 'refused', error_code: 'not_solved', error: 'not_solved: solve first' } as never)
    mount()
    expect((await screen.findByTestId('ic-need-solve')).textContent).toBe('A solved project — Run the solve')
    expect(npvBlock().textContent).not.toMatch(/not_solved|needs: a solved/)
  })

  it('after a run: the figure with its reason, and what it compared', async () => {
    vi.mocked(financeApi.getInvestmentCase).mockResolvedValue({ status: 'done' } as never)
    vi.mocked(financeApi.getReport).mockResolvedValue({ npv_at_wacc: 4_200_000,
      sections: { project: { status: 'ok', payload: { counterfactual: { present: true, counterfactual_basis: 'site_today' } } } } } as never)
    mount()
    expect((await screen.findByTestId('ic-compared')).textContent).toMatch(/^Compared with: /)
    expect(screen.queryByTestId('ic-not-run')).toBeNull()
  })
})
