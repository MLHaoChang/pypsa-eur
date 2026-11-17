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
    expect(await screen.findByText('Obj: € 3.5 m')).toBeTruthy()
    expect(screen.queryByText(/B€/)).toBeNull()
  })

  it('shows it after a reload too, when the store has not seen the solve', async () => {
    vi.mocked(simulationApi.getStatus).mockResolvedValue(
      { status: 'completed', objective: 115678228, dispatch: 'fresh' } as never)
    renderBar()
    expect(await screen.findByText('Obj: € 120 m')).toBeTruthy()
  })

  it('keeps the figure, marked out of date, after an edit since the solve', async () => {
    vi.mocked(simulationApi.getStatus).mockResolvedValue(
      { status: 'completed', objective: 3473806.32, dispatch: 'stale' } as never)
    renderBar()
    expect(await screen.findByText('Obj: € 3.5 m · out of date')).toBeTruthy()
  })

  it('reads a dash when nothing is solved', async () => {
    vi.mocked(simulationApi.getStatus).mockResolvedValue(
      { status: 'idle', objective: null, dispatch: 'none' } as never)
    renderBar()
    await vi.waitFor(() => expect(simulationApi.getStatus).toHaveBeenCalled())
    expect(screen.getByText('Obj: —')).toBeTruthy()
  })
})
