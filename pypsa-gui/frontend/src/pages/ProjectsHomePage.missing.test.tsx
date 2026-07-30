// A project whose folder the user deleted in Finder must SAY so.
//
// Measured in the packaged macOS app: the folder was gone from
// `~/Documents/PyPSA GUI/Projects/`, the app still listed the project as an
// ordinary card reading "0 buses", and clicking Open project hit a 404 with no
// explanation. The backend now sets `missing: true`; this asserts the UI acts
// on it, because a flag nothing renders changes nothing a user sees.
//
// Specific to the LOCAL app. D13 puts projects in human-navigable folders on
// purpose, so deleting one in Finder is the designed workflow meeting its
// obvious consequence — and it cannot happen in the web deployment, where
// nobody has the disk.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import ProjectsHomePage from './ProjectsHomePage'
import { localAdminUser } from '../auth/localMode'
import type { ProjectInfo } from '../api/types'

const authState = { user: localAdminUser(), logout: vi.fn() }
const authMode = { authEnabled: false }
const listed: ProjectInfo[] = []

vi.mock('../auth/AuthProvider', () => ({ useAuth: () => authState }))
vi.mock('../auth/AuthModeProvider', () => ({ useAuthMode: () => authMode }))
vi.mock('../api/projects', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/projects')>()
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      list: vi.fn(async () => listed),
      listUnclaimed: vi.fn(async () => []),
    },
  }
})

function project(over: Partial<ProjectInfo>): ProjectInfo {
  return {
    name: 'Vanishing',
    created_at: new Date().toISOString(),
    has_solver_config: false,
    bus_count: 0,
    snapshot_count: 0,
    objective: null,
    ...over,
  } as ProjectInfo
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

afterEach(() => {
  listed.length = 0
})

describe('a project whose files are gone', () => {
  it('is labelled Files missing instead of looking like an empty project', async () => {
    listed.push(project({ missing: true }))

    renderPage()

    const card = (await screen.findByText('Vanishing')).closest('li') as HTMLElement
    expect(within(card).getByText(/files missing/i)).toBeTruthy()
  })

  it('cannot be opened, since opening it 404s', async () => {
    listed.push(project({ missing: true }))

    renderPage()

    const card = (await screen.findByText('Vanishing')).closest('li') as HTMLElement
    expect(within(card).queryByText(/^Open project$/)).toBeNull()
  })

  it('leaves an ordinary empty project alone', async () => {
    // The mutation this invites is flagging on `bus_count === 0`, which would
    // brand every newly created project as broken.
    listed.push(project({ name: 'BrandNew', missing: false }))

    renderPage()

    const card = (await screen.findByText('BrandNew')).closest('li') as HTMLElement
    expect(within(card).queryByText(/files missing/i)).toBeNull()
    expect(within(card).getByText(/^Open project$/)).toBeTruthy()
  })
})
