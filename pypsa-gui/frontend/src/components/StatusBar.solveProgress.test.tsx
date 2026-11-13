import { act, render, screen } from '@testing-library/react'
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
  vi.mocked(networkApi.getMeta).mockResolvedValue({ bus_count: 1 } as never)
  vi.mocked(networkApi.getSnapshots).mockResolvedValue({ count: 24 } as never)
  vi.mocked(networkApi.undoInfo).mockResolvedValue({ depth: 0, unsaved: false } as never)
  vi.mocked(simulationApi.getStatus).mockResolvedValue({ status: 'idle' } as never)
})

describe('StatusBar solve progress', () => {
  it('says Solving… for an LP run, and nothing when idle', () => {
    useSimulationStore.setState({
      status: 'running', runStartedAt: Date.now(),
      logLines: ['[PHASE] Validation passed (0 warnings).', '[PHASE] Optimising via highs (LOPF)...'],
    })
    const { unmount } = renderBar()
    expect(screen.getByTestId('solve-stage').textContent).toBe('· Solving…')
    unmount()
    useSimulationStore.setState({ status: 'idle', runStartedAt: null })
    renderBar()
    expect(screen.queryByTestId('solve-stage')).toBeNull()
  })

  it('names the stage of a study run in plain words, as a status region without the clock', () => {
    useSimulationStore.setState({ status: 'running', runStartedAt: Date.now(), logLines: ['gridspine ingest 0/6', 'gridspine loadflow 3/6'] })
    renderBar()
    const el = screen.getByTestId('solve-stage')
    expect(el.getAttribute('role')).toBe('status')
    expect(el.textContent).toBe('· Checking the grid · step 4 of 6')
    expect(screen.getByTitle('Elapsed solve time').getAttribute('aria-hidden')).toBe('true')
    expect(el.contains(screen.getByTitle('Elapsed solve time'))).toBe(false)
  })

  it('clears the line when the run ends, and starts from Solving… on a new run', () => {
    useSimulationStore.setState({ status: 'running', runStartedAt: Date.now(), logLines: ['campus size 4/9'] })
    renderBar()
    act(() => useSimulationStore.getState().clearLog())
    expect(screen.getByTestId('solve-stage').textContent).toBe('· Solving…')
    act(() => useSimulationStore.setState({ status: 'failed' }))
    expect(screen.queryByTestId('solve-stage')).toBeNull()
    expect(screen.getByText('Failed')).toBeTruthy()
  })
})
