// UX template audit H2: a result tab's "Run the solve" asks the header to run,
// so the one run path (pre-save, enqueue, log attach) is reused, and it works
// in Guided where the header's idle Run button is hidden.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AppHeader from './AppHeader'
import { useUIStore } from '../store/uiStore'
import { WRITABLE } from '../utils/lockState'

const { enqueue } = vi.hoisted(() => ({ enqueue: vi.fn(async () => ({ status: 'running', position: 0 })) }))

vi.mock('../hooks/useSolveQueue', () => ({
  useSolveQueue: () => ({ data: { jobs: [], running: [], paused: false } }),
  useEnqueueSolve: () => ({ mutateAsync: enqueue, isPending: false }),
  useAbortJob: () => ({ mutateAsync: vi.fn(), isPending: false }),
  activeJobForProject: vi.fn(() => undefined),
}))
vi.mock('../utils/projectActions', async (orig) => ({
  ...(await orig<typeof import('../utils/projectActions')>()),
  saveProjectQuietly: vi.fn(async () => true),
}))
vi.mock('../api/network', () => ({
  networkApi: {
    getBuses: vi.fn().mockResolvedValue([]), getLines: vi.fn().mockResolvedValue([]),
    getGenerators: vi.fn().mockResolvedValue([]), getStorageUnits: vi.fn().mockResolvedValue([]),
    getLoads: vi.fn().mockResolvedValue([]), getLinks: vi.fn().mockResolvedValue([]),
    getMeta: vi.fn().mockResolvedValue({ bus_count: 0 }), undoInfo: vi.fn().mockResolvedValue({ depth: 0 }),
  },
}))
vi.mock('../api/simulation', async (orig) => ({
  ...(await orig<typeof import('../api/simulation')>()),
  simulationApi: {
    getSolverConfig: vi.fn().mockResolvedValue({}),
    getStatus: vi.fn().mockResolvedValue({ running: false }),
  },
  createLogStream: vi.fn(() => () => {}),
}))
vi.mock('../api/projects', () => ({ projectsApi: { list: vi.fn().mockResolvedValue([]) } }))
vi.mock('../auth/AuthModeProvider', () => ({
  useAuthMode: () => ({ ready: true, authEnabled: false, enableAuth: () => {} }),
}))

function renderHeader() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><MemoryRouter><AppHeader /></MemoryRouter></QueryClientProvider>)
}

beforeEach(() => {
  enqueue.mockClear()
  useUIStore.setState({ currentProject: 'demo', projectName: 'demo', uiMode: 'guided' })
  useUIStore.getState().setLockState(WRITABLE)
  useUIStore.getState().setSolvingReadOnly(false)
})

describe('AppHeader — a run requested from elsewhere', () => {
  it('queues the solve once, in Guided, through the header’s own run path', async () => {
    renderHeader()
    await act(async () => { useUIStore.getState().requestRunSolve() })
    await vi.waitFor(() => expect(enqueue).toHaveBeenCalledWith('demo'))
    expect(enqueue).toHaveBeenCalledTimes(1)
  })

  it('does nothing on mount, before any request', async () => {
    renderHeader()
    await act(async () => { await new Promise(r => setTimeout(r, 20)) })
    expect(enqueue).not.toHaveBeenCalled()
  })
})
