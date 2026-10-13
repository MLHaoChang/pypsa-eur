// Guided-mode spec §10 addendum (gate P23 B1): the project-tab "+" quick-create
// makes a NEW project, so G4 applies — noteNewProjectCreated('blank') before
// the new tab opens. Harness copied from ProjectTabs.test.tsx; the first case
// is the QA gate's repro verbatim (qa23/repro/ProjectTabs.qaG4Repro.test.tsx).
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ProjectTabs from './ProjectTabs'
import { useUIStore } from '../store/uiStore'
import { projectsApi } from '../api/projects'
import { switchToProject } from '../utils/projectActions'

vi.mock('../api/projects')
vi.mock('../hooks/useSolveQueue', () => ({
  useSolveQueue: () => ({ data: { jobs: [] } }),
  activeJobForProject: () => undefined,
}))
// Partial mock: the pure helpers (slugify, nextUntitledName) stay real so the
// create path is exercised end to end; only the network-touching ones are
// stubbed.
vi.mock('../utils/projectActions', async (orig) => ({
  ...(await orig<typeof import('../utils/projectActions')>()),
  switchToProject: vi.fn(),
  saveProjectQuietly: vi.fn().mockResolvedValue(true),
  abortRunningSim: vi.fn().mockResolvedValue(true),
  resetBackendNetwork: vi.fn().mockResolvedValue(undefined),
  invalidateNetworkQueries: vi.fn(),
  uniqueProjectName: vi.fn(async (n: string) => n),
}))

const PROJECTS = [
  { name: 'alpha', created_at: '2026-01-01T00:00:00', has_solver_config: true,
    bus_count: 5, snapshot_count: 8760, objective: 1e6 },
  { name: 'beta', created_at: '2026-02-01T00:00:00', has_solver_config: true,
    bus_count: 12, snapshot_count: 24, objective: null },
  { name: 'ghost', created_at: '2026-03-01T00:00:00', has_solver_config: false,
    bus_count: 0, snapshot_count: 0, objective: null, missing: true },
]

const renderTabs = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><ProjectTabs /></QueryClientProvider>)
}

const openDialog = async () => {
  await userEvent.click(screen.getByRole('button', { name: /add a project tab/i }))
  return screen.findByRole('dialog')
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(projectsApi.list).mockResolvedValue(PROJECTS as never)
  vi.mocked(switchToProject).mockResolvedValue({ status: 'switched' } as never)
  useUIStore.setState({
    openTabs: [{ name: 'alpha', lastInteractedAt: 1 }],
    currentProject: 'alpha',
  })
})

describe('QA repro: G4 on the "+" tab create path', () => {
  it('an implicit Expert user who creates a project from the + tab lands in Guided', async () => {
    useUIStore.setState({ uiMode: 'expert', uiModeExplicit: false })
    renderTabs()
    const dlg = await openDialog()
    await userEvent.click(within(dlg).getByRole('tab', { name: 'New project' }))
    const input = within(dlg).getByPlaceholderText('my_project')
    await userEvent.clear(input)
    await userEvent.type(input, 'delta{Enter}')
    await waitFor(() => expect(useUIStore.getState().currentProject).toBe('delta'))
    expect(useUIStore.getState().uiMode).toBe('guided')
  })
})

describe('"+" quick-create — new-project rule', () => {
  it("calls noteNewProjectCreated('blank') before adding the tab; explicit Expert stays", async () => {
    useUIStore.setState({ uiMode: 'expert', uiModeExplicit: true })
    const order: string[] = []
    const realNote = useUIStore.getState().noteNewProjectCreated
    const realAdd = useUIStore.getState().addTab
    useUIStore.setState({
      noteNewProjectCreated: (k) => { order.push(`note:${k}`); realNote(k) },
      addTab: (n) => { order.push(`add:${n}`); realAdd(n) },
    })
    try {
      renderTabs()
      const dlg = await openDialog()
      await userEvent.click(within(dlg).getByRole('tab', { name: 'New project' }))
      const input = within(dlg).getByPlaceholderText('my_project')
      await userEvent.clear(input)
      await userEvent.type(input, 'echo{Enter}')
      await waitFor(() => expect(useUIStore.getState().currentProject).toBe('echo'))
      expect(order.indexOf('note:blank')).toBeGreaterThanOrEqual(0)
      expect(order.indexOf('note:blank')).toBeLessThan(order.indexOf('add:echo'))
      expect(useUIStore.getState().uiMode).toBe('expert')
    } finally {
      useUIStore.setState({ noteNewProjectCreated: realNote, addTab: realAdd })
    }
  })

  it('opening an existing project never changes the mode', async () => {
    useUIStore.setState({ uiMode: 'expert', uiModeExplicit: false })
    renderTabs()
    const dlg = await openDialog()
    await userEvent.click(await within(dlg).findByText('beta'))
    await waitFor(() => expect(vi.mocked(switchToProject)).toHaveBeenCalled())
    expect(useUIStore.getState().uiMode).toBe('expert')
  })
})
