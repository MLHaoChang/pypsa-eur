import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import toast from 'react-hot-toast'
import AppHeader from './AppHeader'
import { useUIStore } from '../store/uiStore'
import { WRITABLE } from '../utils/lockState'

// Guided-mode spec §3.3 — the header's Guided / Expert segmented control.

vi.mock('../hooks/useSolveQueue', () => ({
  useSolveQueue: () => ({ data: { jobs: [], running: [], paused: false } }),
  useEnqueueSolve: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useAbortJob: () => ({ mutateAsync: vi.fn(), isPending: false }),
  activeJobForProject: () => undefined,
}))
vi.mock('../api/network', () => ({
  networkApi: {
    getBuses: vi.fn().mockResolvedValue([]),
    getLines: vi.fn().mockResolvedValue([]),
    getGenerators: vi.fn().mockResolvedValue([]),
    getStorageUnits: vi.fn().mockResolvedValue([]),
    getLoads: vi.fn().mockResolvedValue([]),
    getLinks: vi.fn().mockResolvedValue([]),
    getMeta: vi.fn().mockResolvedValue({ bus_count: 0 }),
    undoInfo: vi.fn().mockResolvedValue({ depth: 0 }),
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
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><AppHeader /></MemoryRouter>
    </QueryClientProvider>,
  )
}

const realSet = useUIStore.getState().setUiMode

beforeEach(() => {
  vi.clearAllMocks()
  useUIStore.setState({ currentProject: 'demo', projectName: 'demo', setUiMode: realSet })
  useUIStore.getState().setLockState(WRITABLE)
  useUIStore.getState().setSolvingReadOnly(false)
  vi.spyOn(toast, 'success').mockImplementation(() => '')
})
afterEach(() => useUIStore.setState({ setUiMode: realSet }))

describe('AppHeader — Guided / Expert switch', () => {
  it('renders both buttons with aria-pressed reflecting the mode', () => {
    useUIStore.setState({ uiMode: 'guided' })
    renderHeader()
    const sw = screen.getByTestId('ui-mode-switch')
    expect(sw).toBeTruthy()
    expect(screen.getByTestId('ui-mode-guided').getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByTestId('ui-mode-expert').getAttribute('aria-pressed')).toBe('false')
  })

  it('reflects Expert too', () => {
    useUIStore.setState({ uiMode: 'expert' })
    renderHeader()
    expect(screen.getByTestId('ui-mode-guided').getAttribute('aria-pressed')).toBe('false')
    expect(screen.getByTestId('ui-mode-expert').getAttribute('aria-pressed')).toBe('true')
  })

  it('carries the spec hover titles', () => {
    renderHeader()
    expect(screen.getByTestId('ui-mode-guided').getAttribute('title')).toBe(
      'A step-by-step hub design with the assistant doing the engineering; advanced panels are hidden but reachable through the assistant.')
    expect(screen.getByTestId('ui-mode-expert').getAttribute('title')).toBe('Every panel and tab, as today.')
  })

  it('sits immediately left of the status pill', () => {
    renderHeader()
    const sw = screen.getByTestId('ui-mode-switch')
    const next = sw.nextElementSibling as HTMLElement
    expect(next.textContent).toMatch(/Idle|Running|Queued|Solved|Failed|Error/)
  })

  it('click calls setUiMode(m, { explicit: true }) and toasts', () => {
    const setUiMode = vi.fn()
    useUIStore.setState({ uiMode: 'guided', setUiMode })
    renderHeader()
    fireEvent.click(screen.getByTestId('ui-mode-expert'))
    expect(setUiMode).toHaveBeenCalledWith('expert', { explicit: true })
    expect(toast.success).toHaveBeenCalledWith('Expert mode on')
    fireEvent.click(screen.getByTestId('ui-mode-guided'))
    expect(setUiMode).toHaveBeenCalledWith('guided', { explicit: true })
    expect(toast.success).toHaveBeenCalledWith(
      'Guided mode on — advanced panels hidden, ask the assistant for any of them')
  })

  it('a real click persists an explicit choice', () => {
    useUIStore.setState({ uiMode: 'guided', uiModeExplicit: false })
    renderHeader()
    fireEvent.click(screen.getByTestId('ui-mode-expert'))
    expect(useUIStore.getState().uiMode).toBe('expert')
    expect(useUIStore.getState().uiModeExplicit).toBe(true)
    expect(localStorage.getItem('network-diagram:ui-mode')).toBe('expert')
    expect(localStorage.getItem('network-diagram:ui-mode-explicit')).toBe('1')
    expect(screen.getByTestId('ui-mode-expert').getAttribute('aria-pressed')).toBe('true')
  })
})

// P24-FE gate decision: the header's "Run LOPF" competes with the hub
// design's own Run in Guided, so it is hidden there while idle; Expert keeps it
// (its snapshot is unchanged).
describe('AppHeader — the Run button per mode', () => {
  it('Guided: no idle Run button', () => {
    useUIStore.setState({ uiMode: 'guided' })
    renderHeader()
    expect(screen.queryByTitle(/queues the solve/)).toBeNull()
  })

  it('Expert: the Run button is there', () => {
    useUIStore.setState({ uiMode: 'expert' })
    renderHeader()
    expect(screen.getByTitle(/queues the solve/)).toBeTruthy()
  })
})
