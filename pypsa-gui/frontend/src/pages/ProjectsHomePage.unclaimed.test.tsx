// Bug 7 (guided-mode spec §2.5): in local (no-auth) mode the page queried
// `/api/projects/unclaimed`, which does not exist there; `listUnclaimed`
// swallowed the 404 but the axios client logged it first — console noise on
// every visit. The query now runs only with auth enabled.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import ProjectsHomePage from './ProjectsHomePage'
import { projectsApi } from '../api/projects'
import { localAdminUser } from '../auth/localMode'

const authState = { user: localAdminUser(), logout: vi.fn() }
const authMode = { authEnabled: false }

vi.mock('../auth/AuthProvider', () => ({ useAuth: () => authState }))
vi.mock('../auth/AuthModeProvider', () => ({ useAuthMode: () => authMode }))
vi.mock('../api/projects', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/projects')>()
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      list: vi.fn(async () => []),
      listUnclaimed: vi.fn(async () => []),
    },
  }
})

async function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter><ProjectsHomePage /></MemoryRouter>
    </QueryClientProvider>,
  )
  // The projects list resolving means the page's initial queries have fired.
  await vi.waitFor(() => expect(projectsApi.list).toHaveBeenCalled())
  await screen.findAllByText(/project/i)
}

afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('unclaimed bundles query (bug 7)', () => {
  it('is not issued in local mode (authEnabled=false)', async () => {
    authMode.authEnabled = false
    await renderPage()
    expect(projectsApi.listUnclaimed).not.toHaveBeenCalled()
  })

  it('is issued when auth is enabled', async () => {
    authMode.authEnabled = true
    await renderPage()
    await vi.waitFor(() => expect(projectsApi.listUnclaimed).toHaveBeenCalledTimes(1))
  })
})
