// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

// Template-e2e 18: where Expert's header Run is the study, the Summary's and the out-of-date banner's
// "Run the solve" still run the dispatch solve whose figures they show; the header's own Run stays the
// study (TE2E-17-RUN-1). The solve toast names its figure the way the status bar does.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AppHeader, { SolvedObjective } from './AppHeader'
import { useUIStore } from '../store/uiStore'
import { WRITABLE } from '../utils/lockState'
import { simulationApi } from '../api/simulation'

const { enqueue, studyRun } = vi.hoisted(() => ({
  enqueue: vi.fn(async () => ({ status: 'running', position: 0 })),
  studyRun: vi.fn(),
}))

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
vi.mock('../hooks/useVerdictStudyEntry', () => ({ useVerdictStudyEntry: () => ({ hasStudy: true }) }))
vi.mock('../pages/hubDesign/useGoalStudy', () => ({
  useGoalStudy: () => ({
    built: { body: {} }, vollMissing: false, problem: null,
    run: { mutate: studyRun, isPending: false },
  }),
}))

function wrap(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><MemoryRouter>{node}</MemoryRouter></QueryClientProvider>)
}

beforeEach(() => {
  enqueue.mockClear()
  studyRun.mockClear()
  useUIStore.setState({ currentProject: 'demo', projectName: 'demo', uiMode: 'expert' })
  useUIStore.getState().setLockState(WRITABLE)
  useUIStore.getState().setSolvingReadOnly(false)
})

describe('AppHeader — Expert with a study', () => {
  it('a dispatch request queues the dispatch solve, not the study', async () => {
    wrap(<AppHeader />)
    await act(async () => { useUIStore.getState().requestDispatchSolve() })
    await vi.waitFor(() => expect(enqueue).toHaveBeenCalledWith('demo'))
    expect(studyRun).not.toHaveBeenCalled()
  })

  it('a plain run request runs the study, as the header Run does', async () => {
    wrap(<AppHeader />)
    await act(async () => { useUIStore.getState().requestRunSolve() })
    await vi.waitFor(() => expect(studyRun).toHaveBeenCalledTimes(1))
    expect(enqueue).not.toHaveBeenCalled()
  })

  it('after a dispatch request, a plain request is the study again', async () => {
    wrap(<AppHeader />)
    await act(async () => { useUIStore.getState().requestDispatchSolve() })
    await vi.waitFor(() => expect(enqueue).toHaveBeenCalledTimes(1))
    await act(async () => { useUIStore.getState().requestRunSolve() })
    await vi.waitFor(() => expect(studyRun).toHaveBeenCalledTimes(1))
    expect(enqueue).toHaveBeenCalledTimes(1)
  })
})

describe('SolvedObjective — the solve toast', () => {
  it('names the LP objective and its discount year, as the status bar does', async () => {
    vi.mocked(simulationApi.getStatus).mockResolvedValueOnce(
      { objective_basis: 'lp_objective', objective_discounted_to: 2027 } as never)
    wrap(<SolvedObjective project="demo" objective="€120.29 M" />)
    expect(await screen.findByText('Solved · LP objective €120.29 M (discounted to 2027)')).toBeTruthy()
  })

  it('names the system cost under myopic foresight or a rolling horizon', async () => {
    vi.mocked(simulationApi.getStatus).mockResolvedValueOnce({ objective_basis: 'system_cost' } as never)
    wrap(<SolvedObjective project="demo" objective="€195 M" />)
    expect(await screen.findByText('Solved · System cost €195 M')).toBeTruthy()
  })

  it('shows the bare figure until the basis is read, and nothing for a missing figure', () => {
    wrap(<SolvedObjective project="demo" objective="€9 M" />)
    expect(screen.getByText('Solved · €9 M')).toBeTruthy()
    wrap(<SolvedObjective project="demo" objective={null} />)
    expect(screen.getByText('Solved')).toBeTruthy()
  })
})
