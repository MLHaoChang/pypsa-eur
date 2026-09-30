// S8: one added projects-home card maps to the wizard's `decision` tab.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import ProjectsHomePage from './ProjectsHomePage'
import { localAdminUser } from '../auth/localMode'

const authState = { user: localAdminUser(), logout: vi.fn() }
const authMode = { authEnabled: false }
vi.mock('../auth/AuthProvider', () => ({ useAuth: () => authState }))
vi.mock('../auth/AuthModeProvider', () => ({ useAuthMode: () => authMode }))
vi.mock('../api/projects', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/projects')>()
  return { ...actual, projectsApi: { ...actual.projectsApi, list: vi.fn(async () => []), listUnclaimed: vi.fn(async () => []) } }
})

afterEach(() => cleanup())

describe('the decision study card', () => {
  it('opens the new-project wizard on the decision tab', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><MemoryRouter><ProjectsHomePage /></MemoryRouter></QueryClientProvider>)
    fireEvent.click(await screen.findByRole('button', { name: /Decision study/ }))
    expect(await screen.findByRole('button', { name: /Start: Do I need a battery/ })).toBeTruthy()
    // No project exists to authorise a study yet: the card says so instead of failing later.
    expect(screen.getByTestId('decision-reason').textContent).toMatch(/Create or open a project first/)
  })
})
