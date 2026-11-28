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
import { useProjectRun } from '../hooks/useProjectRun'
import { useUIStore } from '../store/uiStore'
import { WRITABLE } from '../utils/lockState'
import { simulationApi } from '../api/simulation'
import { money } from '../utils/money'

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
vi.mock('../hooks/useProjectRun', () => ({ useProjectRun: vi.fn(() => null) }))
const studyAbort = vi.fn()
vi.mock('../hooks/useStudyAbort', async (orig) => ({
  ...(await orig<typeof import('../hooks/useStudyAbort')>()),
  useStudyAbort: () => ({ abort: { mutate: studyAbort }, aborting: false }),
}))
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
  studyAbort.mockClear()
  vi.mocked(useProjectRun).mockReturnValue(null)
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

  it('a request while the study runs starts nothing and aborts nothing (GK B3 on #2140)', async () => {
    vi.mocked(useProjectRun).mockReturnValue(
      { name: 'Reliability study', stage: 'Studying', steps: '', startedAt: null, jobId: null, queued: false } as never)
    wrap(<AppHeader />)
    await act(async () => { useUIStore.getState().requestDispatchSolve() })
    await act(async () => { useUIStore.getState().requestRunSolve() })
    await new Promise(r => setTimeout(r, 50))
    expect(enqueue).not.toHaveBeenCalled()
    expect(studyRun).not.toHaveBeenCalled()
    expect(studyAbort).not.toHaveBeenCalled()
    vi.mocked(useProjectRun).mockReturnValue(null)
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
  it('in Guided shows the bare figure: no Expert basis words', async () => {
    useUIStore.setState({ uiMode: 'guided' })
    vi.mocked(simulationApi.getStatus).mockResolvedValue({ objective_basis: 'lp_objective', objective_discounted_to: 2027 } as never)
    wrap(<SolvedObjective project="demo" objective={120_290_000} />)
    await act(async () => { await new Promise(r => setTimeout(r, 20)) })
    expect(document.body.textContent).toBe(`Solved · ${money(120_290_000, 'EUR')}`)
    expect(screen.queryByText(/LP objective/)).toBeNull()
  })

  it('never shows an earlier solve\'s cached basis (#1183): only one read after it opened', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    qc.setQueryData(['simulationStatus', 'demo'], { objective_basis: 'system_cost' }, { updatedAt: Date.now() - 60_000 })
    let resolve: (v: unknown) => void = () => {}
    vi.mocked(simulationApi.getStatus).mockReturnValueOnce(new Promise(r => { resolve = r }) as never)
    render(<QueryClientProvider client={qc}><SolvedObjective project="demo" objective={9_000_000} /></QueryClientProvider>)
    expect(document.body.textContent).toBe(`Solved · ${money(9_000_000, 'EUR')}`)
    await act(async () => { resolve({ objective_basis: 'lp_objective', objective_discounted_to: null }) })
    await vi.waitFor(() => expect(document.body.textContent).toBe(`Solved · LP objective: ${money(9_000_000, 'EUR')}`))
  })

  it('names the LP objective and its discount year, as the status bar does', async () => {
    vi.mocked(simulationApi.getStatus).mockResolvedValueOnce(
      { objective_basis: 'lp_objective', objective_discounted_to: 2027 } as never)
    wrap(<SolvedObjective project="demo" objective={120_290_000} />)
    await vi.waitFor(() => expect(document.body.textContent).toBe(`Solved · LP objective: ${money(120_290_000, 'EUR')} (discounted to 2027)`))
  })

  it('names the system cost under myopic foresight or a rolling horizon', async () => {
    vi.mocked(simulationApi.getStatus).mockResolvedValueOnce({ objective_basis: 'system_cost' } as never)
    wrap(<SolvedObjective project="demo" objective={195_000_000} />)
    await vi.waitFor(() => expect(document.body.textContent).toBe(`Solved · System cost: ${money(195_000_000, 'EUR')}`))
  })

  it('shows the bare figure until the basis is read, and nothing for a missing figure', () => {
    wrap(<SolvedObjective project="demo" objective={9_000_000} />)
    expect(document.body.textContent).toBe(`Solved · ${money(9_000_000, 'EUR')}`)
    wrap(<SolvedObjective project="demo" objective={null} />)
    expect(screen.getByText('Solved')).toBeTruthy()
  })
})
