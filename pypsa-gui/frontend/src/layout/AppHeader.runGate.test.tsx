// W3a: the Run is disabled exactly when the server would refuse, says why by
// section, and its reason opens the field. (Header mocks follow runRequest.)
// UX template audit H2: a result tab's "Run the solve" asks the header to run,
// so the one run path (pre-save, enqueue, log attach) is reused, and it works
// in Guided where the header's idle Run button is hidden.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AppHeader from './AppHeader'
import * as flowApi from '../api/flow'
import { useUIStore } from '../store/uiStore'
import { WRITABLE } from '../utils/lockState'

const { enqueue } = vi.hoisted(() => ({ enqueue: vi.fn(async () => ({ status: 'running', position: 0 })) }))

vi.mock('../api/flow', async (orig) => ({
  ...(await orig<typeof import('../api/flow')>()),
  getProjectFlow: vi.fn(),
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


const target = { section: 'connection', panel: null, component: 'Link', entity: 'grid_import', field: 'p_nom' } as const
const note = { section: 'project', message: 'Project & site: no location',
  target: { section: 'project', panel: 'horizon', component: null, entity: null, field: null } } as const

function flow(over: Record<string, unknown>) {
  return {
    sections: [], run_request: { optimisation: true, study: false }, notes: [], deferred: false,
    deferred_reason: null, run_ready: true, run_blocker: null,
    verdicts: { optimisation: { ready: true, blocker: null }, study: null }, ...over,
  } as never
}

function renderHeader() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><MemoryRouter><AppHeader /></MemoryRouter></QueryClientProvider>)
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'demo', projectName: 'demo', uiMode: 'expert', activeSlidePanel: null })
  useUIStore.getState().setLockState(WRITABLE)
  useUIStore.getState().setSolvingReadOnly(false)
})

describe('AppHeader — the Run gate', () => {
  it('disables Run with the reason by section, and the reason opens the field', async () => {
    const blocker = { section: 'connection', message: 'Connection & grid: no import connection for the reliability study', target }
    vi.mocked(flowApi.getProjectFlow).mockResolvedValue(flow({
      run_ready: false, run_blocker: blocker, verdicts: { optimisation: { ready: false, blocker }, study: null },
    }))
    renderHeader()
    const reason = await screen.findByTestId('run-blocker')
    expect(reason.textContent).toBe(blocker.message)
    const run = screen.getByRole('button', { name: /Run/ }) as HTMLButtonElement
    expect(run.disabled).toBe(true)
    expect(run.title).toContain(blocker.message)
    fireEvent.click(reason)
    expect(useUIStore.getState().selectedComponent).toEqual({ type: 'Link', name: 'grid_import' })
  })

  it('keeps Run enabled with a note for a display-only gap', async () => {
    vi.mocked(flowApi.getProjectFlow).mockResolvedValue(flow({ notes: [note] }))
    renderHeader()
    const n = await screen.findByTestId('run-notes')
    expect(n.textContent).toBe('1 note')
    expect((screen.getByRole('button', { name: /Run/ }) as HTMLButtonElement).disabled).toBe(false)
    expect(screen.queryByTestId('run-blocker')).toBeNull()
  })

  it('does not disable Run when the flow could not be read (the server still refuses)', async () => {
    vi.mocked(flowApi.getProjectFlow).mockRejectedValue(new Error('x'))
    renderHeader()
    await act(async () => { await new Promise(r => setTimeout(r, 20)) })
    expect((screen.getByRole('button', { name: /Run/ }) as HTMLButtonElement).disabled).toBe(false)
  })
})
