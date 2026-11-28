// Control-strategy walkthrough issue 07: the status bar's objective is read
// from /simulation/status (set after every solve and after a reload), in the
// one money notation (utils/money.ts), never as "0.003 B€".
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import StatusBar from './StatusBar'
import { networkApi } from '../api/network'
import { simulationApi } from '../api/simulation'
import { useUIStore } from '../store/uiStore'
import { useSimulationStore } from '../store/simulationStore'

vi.mock('../api/network')
vi.mock('../api/simulation')

function renderBar() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><StatusBar /></QueryClientProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
  useUIStore.setState({ currentProject: 'Alpha', projectName: 'Alpha' })
  useSimulationStore.setState({ status: 'idle', objective: null, runStartedAt: null, logLines: [] })
  vi.mocked(networkApi.getMeta).mockResolvedValue({ bus_count: 1 } as never)
  vi.mocked(networkApi.getSnapshots).mockResolvedValue({ count: 24 } as never)
  vi.mocked(networkApi.undoInfo).mockResolvedValue({ depth: 0, unsaved: false } as never)
})

describe('StatusBar objective (control-strategy 07)', () => {
  it('shows the solved objective from the status endpoint, in the money notation', async () => {
    vi.mocked(simulationApi.getStatus).mockResolvedValue(
      { status: 'completed', objective: 3473806.32, dispatch: 'fresh' } as never)
    renderBar()
    expect(await screen.findByText('LP objective: € 3.5 m')).toBeTruthy()
    expect(screen.queryByText(/B€/)).toBeNull()
  })

  it('shows it after a reload too, when the store has not seen the solve', async () => {
    vi.mocked(simulationApi.getStatus).mockResolvedValue(
      { status: 'completed', objective: 115678228, dispatch: 'fresh' } as never)
    renderBar()
    expect(await screen.findByText('LP objective: € 120 m')).toBeTruthy()
  })

  it('reads a dash once the network has moved past the solve, as Results does', async () => {
    vi.mocked(simulationApi.getStatus).mockResolvedValue(
      { status: 'completed', objective: 3473806.32, dispatch: 'stale' } as never)
    renderBar()
    await vi.waitFor(() => expect(simulationApi.getStatus).toHaveBeenCalled())
    await new Promise(r => setTimeout(r, 0))
    expect(await screen.findByText('LP objective: —')).toBeTruthy()
  })

  it('reads a dash when nothing is solved', async () => {
    vi.mocked(simulationApi.getStatus).mockResolvedValue(
      { status: 'idle', objective: null, dispatch: 'none' } as never)
    renderBar()
    await vi.waitFor(() => expect(simulationApi.getStatus).toHaveBeenCalled())
    expect(await screen.findByText('LP objective: —')).toBeTruthy()
  })

  // Template-e2e 18: the figure names its basis, as Results › Summary does.
  it('says a discounted LP objective is discounted, and to which year', async () => {
    vi.mocked(simulationApi.getStatus).mockResolvedValue({
      status: 'completed', objective: 120293442, dispatch: 'fresh',
      objective_basis: 'lp_objective', objective_discounted_to: 2027,
    } as never)
    renderBar()
    const el = await screen.findByText('LP objective: € 120 m (discounted to 2027)')
    expect(el.getAttribute('title')).toMatch(/^LP objective: discounted to 2027\./)
  })

  it('names a myopic or rolling run\'s figure the system cost', async () => {
    vi.mocked(simulationApi.getStatus).mockResolvedValue({
      status: 'completed', objective: 194866050, dispatch: 'fresh',
      objective_basis: 'system_cost', objective_discounted_to: null,
    } as never)
    renderBar()
    expect(await screen.findByText('System cost: € 190 m')).toBeTruthy()
    expect(screen.queryByText(/LP objective/)).toBeNull()
  })

  it('before the status is read, names the store\'s figure plainly, never "LP objective" (GK B3 on #2140)', async () => {
    vi.mocked(simulationApi.getStatus).mockReturnValue(new Promise(() => {}) as never)
    useSimulationStore.setState({ objective: 194866050 })
    renderBar()
    expect(await screen.findByText('Objective: € 190 m')).toBeTruthy()
    expect(screen.queryByText(/LP objective/)).toBeNull()
  })
})
