// Template-e2e issue 22, slice S2 (GK B3 F5, UX 2): Commercial & finance's pill
// says "Investment case not run yet" only while that result is absent; a
// populated view shows no pill. Other groups keep the solve's freshness.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { financeApi } from '../../api/finance'
import type { InvestmentCaseReportPayload, SimulationStatus } from '../../api/types'
import { useUIStore } from '../../store/uiStore'
import GroupHeader from './GroupHeader'
import { CARD_GROUPS } from './groups'

vi.mock('../../api/finance', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/finance')>()
  return { ...actual, financeApi: { ...actual.financeApi, getReport: vi.fn(), getInvestmentCase: vi.fn() } }
})
const api = vi.mocked(financeApi)
const group = (id: string) => CARD_GROUPS.find(g => g.id === id)!
const SOLVED = { running: false, dispatch: 'fresh', objective: 1 } as unknown as SimulationStatus
const NOT_SOLVED = { running: false, dispatch: 'none', objective: null } as unknown as SimulationStatus

function renderHeader(id: string, status: SimulationStatus) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>
    <GroupHeader group={group(id)} status={status} outsideScope={false} /></QueryClientProvider>)
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  api.getInvestmentCase.mockResolvedValue(null)
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('the Commercial & finance pill', () => {
  it('names the investment case while it has not run, on a solved project', async () => {
    api.getReport.mockResolvedValue(null)
    renderHeader('commercial', SOLVED)
    expect((await screen.findByTestId('results-ghead-freshness')).textContent).toBe('Investment case not run yet')
  })

  it('a first run under way shows no "not run" pill', async () => {
    api.getReport.mockResolvedValue(null)
    api.getInvestmentCase.mockResolvedValue({ status: 'running' })
    renderHeader('commercial', SOLVED)
    await waitFor(() => expect(api.getInvestmentCase).toHaveBeenCalled())
    await new Promise(r => setTimeout(r, 0))
    expect(screen.queryByTestId('results-ghead-freshness')).toBeNull()
  })

  it('a populated view shows no pill, even on an unsolved project', async () => {
    api.getReport.mockResolvedValue({ npv_at_wacc: 1 } as unknown as InvestmentCaseReportPayload)
    renderHeader('commercial', NOT_SOLVED)
    await waitFor(() => expect(api.getReport).toHaveBeenCalled())
    await new Promise(r => setTimeout(r, 0))
    expect(screen.queryByTestId('results-ghead-freshness')).toBeNull()
    expect(screen.queryByText(/Not run yet|not run yet/)).toBeNull()
  })

  it('other groups keep the solve freshness and never read the report', async () => {
    renderHeader('capacity', SOLVED)
    expect(screen.getByTestId('results-ghead-freshness').textContent).toBe('From the latest run')
    expect(api.getReport).not.toHaveBeenCalled()
    expect(api.getInvestmentCase).not.toHaveBeenCalled()
  })
})
