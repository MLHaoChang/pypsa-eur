// The template tab offers the IEEE 39-bus network (increment 5, D3): it is
// gridspine's own detailed grid built by gridspine's own producer, so a
// project made from it, solved and saved, is a valid dispatch source for a
// planning → dynamics study. The card must be there and must create from the
// backend template id the router registers — nothing else about the tab is
// pinned here.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import NewProjectWizard, { type NewProjectTab } from './NewProjectWizard'
import { projectsApi } from '../api/projects'
import { ioApi } from '../api/io'
import { useUIStore } from '../store/uiStore'
import { fetchLocalSettings, type LocalSettingsState } from '../api/localSettings'

vi.mock('../api/projects')
vi.mock('../api/network', () => ({ networkApi: { undoInfo: vi.fn() } }))
vi.mock('../api/gridspine', () => ({ gridspineApi: { createStudy: vi.fn() } }))
vi.mock('../api/io', () => ({ ioApi: { importNetcdf: vi.fn() } }))
vi.mock('../api/localSettings', async (orig) => ({
  ...(await orig<typeof import('../api/localSettings')>()),
  fetchLocalSettings: vi.fn().mockResolvedValue(null),
}))

function renderWizard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <NewProjectWizard existingProjects={[]} onConfirm={() => {}} onClose={() => {}} isPending={false} initialTab="template" />
    </QueryClientProvider>,
  )
}

beforeEach(() => vi.clearAllMocks())

describe('NewProjectWizard — templates', () => {
  it('offers the IEEE 39-bus network and creates from its backend template id', async () => {
    vi.mocked(projectsApi.createFromTemplate).mockResolvedValue({ imported: 'IEEE 39-Bus (New England)', summary: { buses: 39 } } as never)
    renderWizard()
    const card = screen.getByRole('button', { name: /IEEE 39-Bus \(New England\)/ })
    expect(card.textContent).toContain('dispatch source')
    await userEvent.click(card)
    await waitFor(() => expect(projectsApi.createFromTemplate).toHaveBeenCalledWith('ieee39'))
  })
})

// Obstacle 2 (guided-mode spec §2.6): creating a project from /projects left
// the user on /projects — the success handlers set the current project but
// neither added a tab nor opened the workbench. Template, From-file and Clone
// now `addTab` (parity with Sidebar.newProjectMut) and, off /app, navigate to
// `/app?project=<name>`.
describe('NewProjectWizard — a created project opens the workbench', () => {
  let where = ''
  function Where() {
    const loc = useLocation()
    where = loc.pathname + loc.search
    return null
  }
  function renderAt(path: string, tab: NewProjectTab, existing: { name: string }[] = []) {
    window.history.pushState({}, '', path)
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="*" element={<>
              <Where />
              <NewProjectWizard existingProjects={existing as never} onConfirm={() => {}}
                onClose={() => {}} isPending={false} initialTab={tab} />
            </>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )
  }

  // setCurrentProject also appends a tab, so the call itself is what is
  // pinned (parity with Sidebar.newProjectMut), not the resulting list.
  const realAddTab = useUIStore.getState().addTab
  const addTab = vi.fn((name: string) => realAddTab(name))
  beforeEach(() => {
    addTab.mockClear()
    useUIStore.setState({ openTabs: [], addTab })
    vi.mocked(projectsApi.createFromTemplate).mockResolvedValue(
      { imported: 'IEEE 39-Bus (New England)', summary: { buses: 39 } } as never)
  })
  afterEach(() => {
    window.history.pushState({}, '', '/')
    useUIStore.setState({ addTab: realAddTab })
  })

  it('template, from /projects: adds a tab and navigates to /app?project=<name>', async () => {
    renderAt('/projects', 'template')
    await userEvent.click(screen.getByRole('button', { name: /IEEE 39-Bus \(New England\)/ }))
    await waitFor(() => expect(where).toBe('/app?project=IEEE%2039-Bus%20(New%20England)'))
    expect(addTab).toHaveBeenCalledWith('IEEE 39-Bus (New England)')
  })

  it('template, from /app: adds a tab and does not navigate', async () => {
    renderAt('/app', 'template')
    await userEvent.click(screen.getByRole('button', { name: /IEEE 39-Bus \(New England\)/ }))
    await waitFor(() => expect(addTab).toHaveBeenCalledWith('IEEE 39-Bus (New England)'))
    expect(where).toBe('/app')
  })

  it('from file (.nc), from /projects: adds a tab and navigates', async () => {
    vi.mocked(projectsApi.list).mockResolvedValue([])
    vi.mocked(ioApi.importNetcdf).mockResolvedValue({ buses: 3 } as never)
    vi.mocked(projectsApi.save).mockResolvedValue({ saved: 'grid' } as never)
    renderAt('/projects', 'file')
    const input = document.querySelector('input[type=file]') as HTMLInputElement
    await userEvent.upload(input, new File(['x'], 'grid.nc'))
    await waitFor(() => expect(where).toBe('/app?project=grid'))
    expect(addTab).toHaveBeenCalledWith('grid')
  })

  it('clone, from /projects: adds a tab and navigates', async () => {
    const src = { id: 'id-src', name: 'src', created_at: '2026-01-01T00:00:00',
      has_solver_config: true, bus_count: 5, snapshot_count: 24, objective: null }
    vi.mocked(projectsApi.list).mockResolvedValue([src] as never)
    vi.mocked(projectsApi.load).mockResolvedValue({} as never)
    vi.mocked(projectsApi.save).mockResolvedValue({ saved: 'src_copy' } as never)
    renderAt('/projects', 'clone', [src])
    await userEvent.click(await screen.findByRole('button', { name: /^src/ }))
    await userEvent.click(await screen.findByRole('button', { name: 'Clone project' }))
    await waitFor(() => expect(where).toBe('/app?project=src_copy'))
    expect(addTab).toHaveBeenCalledWith('src_copy')
  })
})

// P30 (B6): the Blank tab's "Saved to" line shows the directory the backend
// actually saves into (`GET /local-settings` → `projects_root`, local mode
// only). A hosted deployment answers 404 (mapped to null by
// `fetchLocalSettings`) and a failed read shows nothing: no line, rather
// than a path that is not true.
describe('NewProjectWizard — where a blank project is saved (P30 B6)', () => {
  function renderBlank() {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={qc}>
        <NewProjectWizard existingProjects={[]} onConfirm={() => {}} onClose={() => {}}
          isPending={false} initialTab="blank" />
      </QueryClientProvider>,
    )
  }
  const STATE: LocalSettingsState = { key_set: false, key_hint: null, key_redactable: null,
    log_path: '/data/pypsa-gui.log', projects_root: '/home/me/Documents/PyPSA Studio/Projects' }

  it("shows the backend's root", async () => {
    vi.mocked(fetchLocalSettings).mockResolvedValue(STATE)
    renderBlank()
    const line = await screen.findByTestId('new-project-saved-to')
    expect(line.textContent).toBe('Saved to /home/me/Documents/PyPSA Studio/Projects/new_project/')
    expect(screen.queryByText(/pypsa-gui\/backend\/projects/)).toBeNull()
  })

  it('no line in hosted mode (the route 404s → null)', async () => {
    vi.mocked(fetchLocalSettings).mockResolvedValue(null)
    renderBlank()
    await waitFor(() => expect(fetchLocalSettings).toHaveBeenCalled())
    await new Promise(r => setTimeout(r, 20))
    expect(screen.queryByTestId('new-project-saved-to')).toBeNull()
    expect(screen.queryByText(/Saved to/)).toBeNull()
  })

  it('no line when the query fails', async () => {
    vi.mocked(fetchLocalSettings).mockRejectedValue(new Error('Network Error'))
    renderBlank()
    await waitFor(() => expect(fetchLocalSettings).toHaveBeenCalled())
    await new Promise(r => setTimeout(r, 20))
    expect(screen.queryByTestId('new-project-saved-to')).toBeNull()
    expect(screen.queryByText(/Saved to/)).toBeNull()
  })
})
