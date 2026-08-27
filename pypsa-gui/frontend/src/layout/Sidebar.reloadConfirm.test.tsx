// `handleOpenPick`'s `name === currentProject` branch is the "stale frontend,
// empty backend" recovery re-load. Its own comment calls it "a DESTRUCTIVE
// re-load" — it forces a disk re-read, so anything unsaved in the backend's
// in-memory network is gone. It ran with no confirmation.
//
// The confirm is gated on the dirty state rather than unconditional: the
// recovery case this path exists for is precisely the one where the backend
// network is EMPTY, and prompting there would be noise on the exact workflow
// the branch was built to serve. But "clean" has to mean clean, not merely
// unanswered — a failed undo probe fails CLOSED, same rule as the import
// guard (86d0fe00) and the save-state dot (7cedd7a8).
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import toast from 'react-hot-toast'
import Sidebar from './Sidebar'
import { useUIStore } from '../store/uiStore'
import { WRITABLE } from '../utils/lockState'
import { projectsApi } from '../api/projects'
import { networkApi } from '../api/network'
import type { ProjectInfo } from '../api/types'

vi.mock('../hooks/useSolveQueue', () => ({
  useSolveQueue: () => ({ data: { jobs: [], current: null } }),
}))
vi.mock('../hooks/useLocalSettings', () => ({
  useLocalSettingsAvailable: () => false,
}))
vi.mock('../api/projects')
vi.mock('../utils/projectActions', async (orig) => ({
  ...(await orig<typeof import('../utils/projectActions')>()),
  abortRunningSim: vi.fn().mockResolvedValue(true),
}))

const DEMO: ProjectInfo = {
  id: 'id-demo', name: 'demo', created_at: '2026-01-01T00:00:00',
  has_solver_config: true, bus_count: 3, snapshot_count: 24, objective: null,
}

function renderSidebar() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><Sidebar /></MemoryRouter>
    </QueryClientProvider>,
  )
}

async function pickCurrentProject() {
  window.dispatchEvent(new CustomEvent('chat:open-project-picker'))
  await userEvent.click(await screen.findByTitle("Open 'demo'"))
}

beforeEach(() => {
  vi.clearAllMocks()
  useUIStore.setState({ currentProject: 'demo', projectName: 'demo', sidebarMode: 'expanded', recents: [] })
  useUIStore.getState().setLockState(WRITABLE)
  useUIStore.getState().setSolvingReadOnly(false)
  vi.mocked(projectsApi.list).mockResolvedValue([DEMO])
  vi.mocked(projectsApi.load).mockResolvedValue({} as never)
  vi.spyOn(toast, 'error').mockImplementation(() => '')
  vi.spyOn(toast, 'success').mockImplementation(() => '')
  vi.spyOn(toast, 'loading').mockImplementation(() => 'tid')
  vi.spyOn(toast, 'dismiss').mockImplementation(() => {})
})

describe('destructive re-load of the current project', () => {
  it('asks before discarding unsaved work', async () => {
    vi.spyOn(networkApi, 'undoInfo').mockResolvedValue({ depth: 3 } as never)
    renderSidebar()
    await pickCurrentProject()

    expect(projectsApi.load).not.toHaveBeenCalled()
    await screen.findByRole('dialog')

    await userEvent.click(screen.getByRole('button', { name: /reload|re-load/i }))
    await waitFor(() => expect(projectsApi.load).toHaveBeenCalledWith('demo'))
  })

  it('asks when the dirty state could not be determined (fails closed)', async () => {
    vi.spyOn(networkApi, 'undoInfo').mockRejectedValue(new Error('unreachable'))
    renderSidebar()
    await pickCurrentProject()

    expect(projectsApi.load).not.toHaveBeenCalled()
    const dialog = await screen.findByRole('dialog')
    expect(dialog.textContent).toMatch(/could not|unsaved/i)
  })

  it('does not re-load when cancelled', async () => {
    vi.spyOn(networkApi, 'undoInfo').mockResolvedValue({ depth: 3 } as never)
    renderSidebar()
    await pickCurrentProject()
    await screen.findByRole('dialog')

    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(projectsApi.load).not.toHaveBeenCalled()
  })

  it('re-loads a genuinely clean project without prompting', async () => {
    vi.spyOn(networkApi, 'undoInfo').mockResolvedValue({ depth: 0 } as never)
    renderSidebar()
    await pickCurrentProject()

    await waitFor(() => expect(projectsApi.load).toHaveBeenCalledWith('demo'))
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})
