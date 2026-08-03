// The "+" tab used to be create-only: there was no way to open a project you
// already had beside the current one without going to the sidebar. These
// cover the two-mode dialog that replaced it, plus the store invariant the
// whole feature rests on (opening ADDS a tab, it does not replace one).
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

describe('ProjectTabs "+" dialog', () => {
  it('defaults to Open existing and lists the backend projects', async () => {
    renderTabs()
    const dlg = await openDialog()
    expect(within(dlg).getByRole('tab', { name: 'Open existing' }))
      .toHaveProperty('ariaSelected', 'true')
    expect(await within(dlg).findByText('beta')).toBeTruthy()
  })

  it('opens an existing project instead of only offering to create one', async () => {
    // The actual complaint: "+" gave no way to load a project you already had.
    renderTabs()
    const dlg = await openDialog()
    await userEvent.click(await within(dlg).findByText('beta'))
    await waitFor(() =>
      expect(vi.mocked(switchToProject)).toHaveBeenCalledWith('beta', expect.anything()))
  })

  it('marks the project that is already open rather than hiding it', async () => {
    renderTabs()
    const dlg = await openDialog()
    const row = (await within(dlg).findByText(/^alpha/)).closest('button')!
    expect(within(row).getByText('(current)')).toBeTruthy()
  })

  it('refuses to open a project whose files are gone', async () => {
    // The registry row outlives a folder deleted in Finder; opening one 404s.
    renderTabs()
    const dlg = await openDialog()
    const row = (await within(dlg).findByText('ghost')).closest('button')!
    expect(row).toHaveProperty('disabled', true)
    await userEvent.click(row)
    expect(vi.mocked(switchToProject)).not.toHaveBeenCalled()
  })

  it('falls back to New project when the backend has nothing to open', async () => {
    vi.mocked(projectsApi.list).mockResolvedValue([] as never)
    renderTabs()
    const dlg = await openDialog()
    await waitFor(() =>
      expect(within(dlg).getByRole('tab', { name: 'New project' }))
        .toHaveProperty('ariaSelected', 'true'))
    expect(within(dlg).getByPlaceholderText('my_project')).toBeTruthy()
  })

  it('still creates a new project from the other mode', async () => {
    renderTabs()
    const dlg = await openDialog()
    await userEvent.click(within(dlg).getByRole('tab', { name: 'New project' }))
    const input = within(dlg).getByPlaceholderText('my_project')
    await userEvent.clear(input)
    await userEvent.type(input, 'gamma{Enter}')
    await waitFor(() =>
      expect(vi.mocked(projectsApi.save)).toHaveBeenCalledWith('gamma', true))
  })

  it('does not let a chosen mode be overwritten when the list settles', async () => {
    // The default is derived from the project list, which resolves async and
    // refetches. Re-deriving it every render would yank the user out of the
    // mode they just picked.
    renderTabs()
    const dlg = await openDialog()
    await userEvent.click(within(dlg).getByRole('tab', { name: 'New project' }))
    await new Promise(r => setTimeout(r, 20))
    expect(within(dlg).getByRole('tab', { name: 'New project' }))
      .toHaveProperty('ariaSelected', 'true')
  })
})

describe('opening a project adds a tab rather than replacing one', () => {
  it('setCurrentProject keeps the existing tab open', () => {
    // This store behaviour is what makes "open beside" work at all — the
    // dialog just routes into it via switchToProject.
    useUIStore.setState({
      openTabs: [{ name: 'alpha', lastInteractedAt: 1 }],
      currentProject: 'alpha',
    })
    useUIStore.getState().setCurrentProject('beta')
    const names = useUIStore.getState().openTabs.map(t => t.name)
    expect(names).toEqual(['alpha', 'beta'])
    expect(useUIStore.getState().currentProject).toBe('beta')
  })

  it('does not duplicate a tab that is already open', () => {
    useUIStore.setState({
      openTabs: [{ name: 'alpha', lastInteractedAt: 1 },
                 { name: 'beta', lastInteractedAt: 2 }],
      currentProject: 'alpha',
    })
    useUIStore.getState().setCurrentProject('beta')
    expect(useUIStore.getState().openTabs.map(t => t.name)).toEqual(['alpha', 'beta'])
  })
})
