// UX assessment Q6 / D-4: after a solve the header said "Optimal" while the
// Run button still said "Abort" (the queue poll lags the log stream's `done`
// by up to one poll), and nothing told the user where the answer was.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import toast from 'react-hot-toast'
import AppHeader, { formatObjective } from './AppHeader'
import { useUIStore } from '../store/uiStore'
import { useSimulationStore } from '../store/simulationStore'
import { WRITABLE } from '../utils/lockState'
import { createLogStream } from '../api/simulation'

let queueJobs: Array<{ id: string; project_id: string; status: string; position: number | null }> = []
vi.mock('../hooks/useSolveQueue', () => ({
  useSolveQueue: () => ({ data: { jobs: queueJobs, running: [], paused: false } }),
  useEnqueueSolve: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useAbortJob: () => ({ mutateAsync: vi.fn(), isPending: false }),
  activeJobForProject: (list: { jobs: typeof queueJobs } | undefined, name: string) =>
    list?.jobs.find(j => j.project_id === name && (j.status === 'queued' || j.status === 'running')),
}))

vi.mock('../api/simulation', async (orig) => ({
  ...(await orig<typeof import('../api/simulation')>()),
  createLogStream: vi.fn(() => () => {}),
}))

vi.mock('../auth/AuthModeProvider', () => ({
  useAuthMode: () => ({ ready: true, authEnabled: false, enableAuth: () => {} }),
}))

function renderHeader() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><AppHeader /></MemoryRouter>
    </QueryClientProvider>,
  )
}

/** The `done` callback AppHeader handed to the (mocked) log stream. */
function doneCallback(): (data: unknown) => void {
  const calls = vi.mocked(createLogStream).mock.calls
  return calls[calls.length - 1][1] as (data: unknown) => void
}

beforeEach(() => {
  vi.clearAllMocks()
  queueJobs = [{ id: 'job-1', project_id: 'Demo', status: 'running', position: null }]
  useUIStore.setState({
    currentProject: 'Demo', projectName: 'Demo', uiMode: 'expert',
    activeSlidePanel: null, resultsTabRequest: null,
  })
  useUIStore.getState().setLockState(WRITABLE)
  useSimulationStore.setState({ status: 'idle' })
})

describe('after a successful solve', () => {
  it('stops showing Abort the moment the solve says done, before the queue catches up', async () => {
    renderHeader()
    expect(await screen.findByText('Abort')).toBeTruthy()
    // The queue mock still reports job-1 as running: the stale-poll window.
    act(() => doneCallback()({ status: 'completed', objective: 1_234_567 }))
    expect(screen.queryByText('Abort')).toBeNull()
    expect(screen.getByText('Optimal')).toBeTruthy()
  })

  it('toasts the objective and opens Results on Economics when nothing else is open', async () => {
    const success = vi.spyOn(toast, 'success')
    renderHeader()
    await screen.findByText('Abort')
    act(() => doneCallback()({ status: 'completed', objective: 1_234_567 }))
    expect(success).toHaveBeenCalled()
    expect(useUIStore.getState().activeSlidePanel).toBe('results')
    expect(useUIStore.getState().resultsTabRequest).toBe('economics')
  })

  it('leaves a panel the user opened during the solve where it is', async () => {
    useUIStore.setState({ activeSlidePanel: 'timeseries' })
    renderHeader()
    await screen.findByText('Abort')
    act(() => doneCallback()({ status: 'completed', objective: 10 }))
    expect(useUIStore.getState().activeSlidePanel).toBe('timeseries')
  })
})

describe('formatObjective', () => {
  it.each([
    [1_234_567, '€1.23 M'], [830_400, '€830 k'], [12, '€12'], [2.5e9, '€2.50 bn'],
  ])('%s → %s', (v, out) => expect(formatObjective(v)).toBe(out))

  it('says nothing for a missing objective rather than €0', () => {
    expect(formatObjective(null)).toBeNull()
    expect(formatObjective(Number.NaN)).toBeNull()
  })
})
