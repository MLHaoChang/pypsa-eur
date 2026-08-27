// Correction 1's milder sibling: `handleOpenPick`'s `name === currentProject`
// branch (Sidebar.tsx) is the "stale frontend, empty backend" recovery
// reload — it calls `projectsApi.load`, which IS `load_project`, the route
// this branch now refuses (409, error_kind `solver_in_flight`) while a queue
// job owns the project's context. The catch block used to swallow that into
// a hardcoded `Could not open '<name>'`, hiding exactly the reason this
// whole fix exists to surface — same defect class as the clone wizard
// (NewProjectWizard.clone.test.tsx), milder because there's no false
// recovery instruction attached, just a lost message.
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
import type { ProjectInfo } from '../api/types'

vi.mock('../hooks/useSolveQueue', () => ({
  useSolveQueue: () => ({ data: { jobs: [], running: [], paused: false } }),
}))
vi.mock('../hooks/useLocalSettings', () => ({
  useLocalSettingsAvailable: () => false,
}))
vi.mock('../api/projects')
// Real network probe (`getLockStatus`) has nothing to talk to in jsdom;
// stubbing `abortRunningSim` (as ProjectTabs.test.tsx already does) skips
// that entirely rather than relying on its network-failure fallback.
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

beforeEach(() => {
  vi.clearAllMocks()
  useUIStore.setState({ currentProject: 'demo', projectName: 'demo', sidebarMode: 'expanded', recents: [] })
  useUIStore.getState().setLockState(WRITABLE)
  useUIStore.getState().setSolvingReadOnly(false)
  vi.mocked(projectsApi.list).mockResolvedValue([DEMO])
  vi.spyOn(toast, 'error').mockImplementation(() => '')
  vi.spyOn(toast, 'success').mockImplementation(() => '')
  vi.spyOn(toast, 'loading').mockImplementation(() => 'tid')
})

describe('reopening the current project while a queue job owns it', () => {
  it('surfaces the server refusal reason instead of a bare "Could not open"', async () => {
    vi.mocked(projectsApi.load).mockRejectedValueOnce({
      message: 'Request failed with status code 409',
      response: {
        status: 409,
        data: {
          detail: {
            error_kind: 'solver_in_flight',
            message: "'demo' is being solved by the queue right now.",
          },
        },
      },
    })
    renderSidebar()

    // The picker modal is normally reached via chat's "browse projects"
    // affordance. It's the only direct-UI route to the name===currentProject
    // reload branch — the sidebar's own "Recent" list filters OUT the
    // current project, so it can never retarget itself.
    window.dispatchEvent(new CustomEvent('chat:open-project-picker'))

    await userEvent.click(await screen.findByTitle("Open 'demo'"))

    await waitFor(() => expect(vi.mocked(toast.error)).toHaveBeenCalled())
    const calls = vi.mocked(toast.error).mock.calls
    const shown = String(calls[calls.length - 1]?.[0])
    expect(shown).toContain('is being solved by the queue right now')
    expect(shown).not.toBe("Could not open 'demo'")
  })
})
