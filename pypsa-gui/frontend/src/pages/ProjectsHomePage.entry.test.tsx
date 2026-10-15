// UX overhaul S7 (findings P1, P2, P4): the projects list comes first, the
// page speaks without tenancy jargon, the creation cards use the wizard's
// words, and a decision study says up front where it is available.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import ProjectsHomePage from './ProjectsHomePage'
import { localAdminUser } from '../auth/localMode'
import { CREATE_LABEL } from '../layout/newProjectVocabulary'

const authState = { user: localAdminUser(), logout: vi.fn() }
const authMode = { authEnabled: false }

vi.mock('../auth/AuthProvider', () => ({ useAuth: () => authState }))
vi.mock('../auth/AuthModeProvider', () => ({ useAuthMode: () => authMode }))
vi.mock('../components/AssistantDock', () => ({ default: () => <div /> }))
vi.mock('../api/projects', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/projects')>()
  return {
    ...actual,
    projectsApi: { ...actual.projectsApi, list: vi.fn(async () => []), listUnclaimed: vi.fn(async () => []) },
  }
})
vi.mock('../layout/NewProjectWizard', () => ({ default: () => null }))

afterEach(() => { cleanup(); authMode.authEnabled = false })

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/projects']}><ProjectsHomePage /></MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('ProjectsHomePage — entry (UX S7)', () => {
  it('lists the projects before the ways to start one', async () => {
    renderPage()
    const list = await screen.findByRole('heading', { name: 'Projects you can open' })
    const start = screen.getByRole('heading', { name: 'Start a project' })
    expect(list.compareDocumentPosition(start) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('drops the tenancy jargon above the list', async () => {
    renderPage()
    await screen.findByRole('heading', { name: 'Projects you can open' })
    expect(screen.queryByText(/root projects are listed/i)).toBeNull()
    expect(screen.queryByText(/rather than in the workbench/i)).toBeNull()
    expect(screen.queryByText('Accessible roots')).toBeNull()
  })

  it('names the creation cards with the wizard’s words', async () => {
    renderPage()
    for (const kind of ['blank', 'template', 'file', 'clone', 'decision'] as const) {
      expect(await screen.findByRole('button', { name: new RegExp(`^${CREATE_LABEL[kind]}`) })).toBeTruthy()
    }
  })

  it('says a decision study needs the desktop app when accounts are on, rather than failing after the click', async () => {
    authMode.authEnabled = true
    renderPage()
    const card = await screen.findByRole('button', { name: new RegExp(`^${CREATE_LABEL.decision}`) })
    expect((card as HTMLButtonElement).disabled).toBe(true)
    expect(card.textContent).toMatch(/desktop app/i)
  })
})
