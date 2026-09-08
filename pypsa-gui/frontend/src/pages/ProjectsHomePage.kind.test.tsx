// A planning → dynamics study is its own project kind (increment 4). It lives
// in the same registry as every capacity-expansion project, so the projects
// home lists it beside them — and a card that looked identical would invite
// opening it in the network workbench, where there is no network to show.
// The badge comes from the backend's `project_kind`, never from the name.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ProjectInfo } from '../api/types'
import ProjectsHomePage from './ProjectsHomePage'
import { localAdminUser } from '../auth/localMode'

const authState = { user: localAdminUser(), logout: vi.fn() }
const authMode = { authEnabled: false }
const rows = vi.hoisted(() => ({ list: [] as unknown[] }))

vi.mock('../auth/AuthProvider', () => ({ useAuth: () => authState }))
vi.mock('../auth/AuthModeProvider', () => ({ useAuthMode: () => authMode }))
vi.mock('../api/projects', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/projects')>()
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      list: vi.fn(async () => rows.list),
      listUnclaimed: vi.fn(async () => []),
    },
  }
})

const base: ProjectInfo = {
  id: '1', name: 'x', created_at: '2026-09-01T00:00:00Z', has_solver_config: false,
  bus_count: 0, snapshot_count: 0, objective: null, parent_project: null,
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ProjectsHomePage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => cleanup())

describe('the project kind badge', () => {
  it('marks a planning → dynamics study, and nothing else', async () => {
    rows.list = [
      { ...base, id: '1', name: 'Winter study', project_kind: 'planning_dynamics' },
      { ...base, id: '2', name: 'Plain project', project_kind: null },
      { ...base, id: '3', name: 'Old row' },                    // pre-migration: no field at all
    ]
    renderPage()
    const study = (await screen.findByText('Winter study')).closest('article')!
    expect(within(study).getByText('Study')).toBeTruthy()
    expect(within(study).getByText('Root')).toBeTruthy()      // a study is still a root project
    for (const name of ['Plain project', 'Old row']) {
      const card = screen.getByText(name).closest('article')!
      expect(within(card).queryByText('Study')).toBeNull()
      expect(within(card).getByText('Root')).toBeTruthy()
    }
  })
})
