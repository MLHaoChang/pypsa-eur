import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import toast from 'react-hot-toast'
import AppHeader from './AppHeader'
import { useUIStore } from '../store/uiStore'
import { WRITABLE } from '../utils/lockState'
import { activeJobForProject } from '../hooks/useSolveQueue'
import { useSimulationStore } from '../store/simulationStore'
import { UI_MODE_TITLES } from '../utils/uiMode'

// Guided-mode spec §3.3 — the header's Guided / Expert segmented control.

vi.mock('../hooks/useSolveQueue', () => ({
  useSolveQueue: () => ({ data: { jobs: [], running: [], paused: false } }),
  useEnqueueSolve: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useAbortJob: () => ({ mutateAsync: vi.fn(), isPending: false }),
  activeJobForProject: vi.fn(() => undefined),
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

  // P30 (B9): the hover title is not read by screen readers on focus; each
  // button points at a visually hidden sentence carrying the same words.
  it('each mode button has an accessible description equal to its title', () => {
    renderHeader()
    for (const m of ['guided', 'expert'] as const) {
      const btn = screen.getByTestId(`ui-mode-${m}`)
      const id = btn.getAttribute('aria-describedby')
      expect(id).toBe(`ui-mode-${m}-desc`)
      const desc = document.getElementById(id!)
      expect(desc).not.toBeNull()
      expect(desc!.textContent).toBe(UI_MODE_TITLES[m])
      expect(desc!.className).toContain('sr-only')
      expect(btn.getAttribute('title')).toBe(UI_MODE_TITLES[m])
      expect(btn.textContent).toBe(m === 'guided' ? 'Guided' : 'Expert')   // name unchanged
    }
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

  // Re-gate R15: the safety half — a solve in flight stays abortable.
  it('Guided: a running solve still shows Abort', () => {
    useUIStore.setState({ uiMode: 'guided' })
    useSimulationStore.setState({ status: 'running' })
    try {
      renderHeader()
      expect(screen.getByTitle('Abort the running simulation').textContent).toContain('Abort')
    } finally { useSimulationStore.setState({ status: 'idle' }) }
  })

  it('Guided: a queued solve still shows its cancel button', () => {
    useUIStore.setState({ uiMode: 'guided' })
    vi.mocked(activeJobForProject).mockReturnValue(
      { project_id: 'demo', status: 'queued', position: 2 } as never)
    try {
      renderHeader()
      expect(screen.getByTitle('Cancel this queued solve').textContent).toContain('Queued #2')
    } finally { vi.mocked(activeJobForProject).mockReturnValue(undefined) }
  })

  // P30 (C11, R15): a solve the queue reports as running (the store starts
  // idle; the header follows the queue) keeps the Abort control in Guided.
  it('Guided: a running queue job still shows Abort', () => {
    useUIStore.setState({ uiMode: 'guided' })
    vi.mocked(activeJobForProject).mockReturnValue(
      { project_id: 'demo', status: 'running', position: null } as never)
    try {
      renderHeader()
      expect(screen.getByTitle('Abort the running simulation').textContent).toContain('Abort')
    } finally {
      vi.mocked(activeJobForProject).mockReturnValue(undefined)
      useSimulationStore.setState({ status: 'idle' })
    }
  })

  it('Expert: the Run button is there', () => {
    useUIStore.setState({ uiMode: 'expert' })
    renderHeader()
    expect(screen.getByTitle(/queues the solve/)).toBeTruthy()
  })
})
