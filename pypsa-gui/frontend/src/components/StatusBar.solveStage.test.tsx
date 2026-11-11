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
  vi.mocked(networkApi.getMeta).mockResolvedValue({ bus_count: 1 } as never)
  vi.mocked(networkApi.getSnapshots).mockResolvedValue({ count: 24 } as never)
  vi.mocked(networkApi.undoInfo).mockResolvedValue({ depth: 0, unsaved: false } as never)
  vi.mocked(simulationApi.getStatus).mockResolvedValue({ status: 'idle' } as never)
})

describe('StatusBar solve stage', () => {
  it('names the stage and step while a solve runs, and nothing when idle', () => {
    useSimulationStore.setState({
      status: 'running', runStartedAt: Date.now(),
      logLines: ['[PHASE] Validation passed (0 warnings).', '[PHASE] Optimising via highs (LOPF)...'],
    })
    const { unmount } = renderBar()
    expect(screen.getByTestId('solve-stage').textContent).toContain('Solving, step 3 of 4')
    unmount()
    useSimulationStore.setState({ status: 'idle', runStartedAt: null })
    renderBar()
    expect(screen.queryByTestId('solve-stage')).toBeNull()
  })
})
