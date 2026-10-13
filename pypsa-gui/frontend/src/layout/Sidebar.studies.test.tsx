import { beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { projectsApi } from '../api/projects'
import Sidebar from './Sidebar'

vi.mock('../api/network', () => ({
  networkApi: { getMeta: vi.fn().mockResolvedValue({ bus_count: 3 }) },
}))
vi.mock('../api/io', () => ({ ioApi: {} }))
vi.mock('../api/projects', () => ({
  projectsApi: { list: vi.fn(), save: vi.fn() },
}))
vi.mock('../api/simulation', () => ({
  simulationApi: { preflight: vi.fn().mockResolvedValue({ ok: true, errors: 0, warnings: 0, issues: [] }) },
}))
vi.mock('../api/solveQueue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/solveQueue')>()
  return { ...actual, solveQueueApi: { list: vi.fn().mockResolvedValue({ jobs: [] }) } }
})
vi.mock('../api/localSettings', () => ({
  fetchLocalSettings: vi.fn().mockResolvedValue({ api_key_hint: null, log_path: '/tmp/app.log' }),
}))
vi.mock('../pages/ImportExport', () => ({ ImportZone: () => <div /> }))
vi.mock('./NewProjectWizard', () => ({ default: () => <div /> }))
vi.mock('../components/ProjectPicker', () => ({ default: () => <div /> }))
vi.mock('../utils/projectActions', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../utils/projectActions')>()
  return {
    ...actual,
    invalidateNetworkQueries: vi.fn(),
    saveProjectQuietly: vi.fn(),
    resetBackendNetwork: vi.fn(),
    downloadProjectBundle: vi.fn(),
    abortRunningSim: vi.fn(),
    switchToProject: vi.fn(),
  }
})

function renderSidebar() {
  // retry: false — a rejected preflight mock must surface as an error on
  // the first attempt, not get silently retried away in the test.
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

// UX assessment Q10: Planning → dynamics only works in a study project
// (project_kind 'planning_dynamics'); anywhere else its panel is a "not a
// study" dead end, so the row is hidden — but only when the project list says
// so. Unknown (list loading, failed, or the project not in it) keeps the row.
function project(name: string, project_kind?: string) {
  return { id: name, name, project_kind } as never
}

beforeEach(() => {
  cleanup()
  useUIStore.setState({ sidebarMode: 'expanded', currentProject: 'Demo', projectName: 'Demo', activeSlidePanel: null })
})

describe('Planning → dynamics row', () => {
  it('is hidden in a capacity-expansion project', async () => {
    vi.mocked(projectsApi.list).mockResolvedValue([project('Demo', 'capacity_expansion')])
    renderSidebar()
    await screen.findByText('Campus electrical')
    await waitFor(() => expect(screen.queryByText('Planning → dynamics')).toBeNull())
  })

  it('is shown in a study project', async () => {
    vi.mocked(projectsApi.list).mockResolvedValue([project('Demo', 'planning_dynamics')])
    renderSidebar()
    expect(await screen.findByText('Planning → dynamics')).toBeTruthy()
  })

  it('stays when the open project is not in the list (unknown is not "not a study")', async () => {
    vi.mocked(projectsApi.list).mockResolvedValue([project('Other', 'capacity_expansion')])
    renderSidebar()
    await screen.findByText('Campus electrical')
    await waitFor(() => expect(vi.mocked(projectsApi.list)).toHaveBeenCalled())
    expect(screen.getByText('Planning → dynamics')).toBeTruthy()
  })
})
