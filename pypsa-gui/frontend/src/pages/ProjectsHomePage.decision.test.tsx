// S8: one added projects-home card maps to the wizard's `decision` tab.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import ProjectsHomePage from './ProjectsHomePage'
import { localAdminUser } from '../auth/localMode'
import { decisionStudiesApi } from '../api/decisionStudies'
import { STUDY_REF_KEY, useDecisionStore } from './decision/decisionStore'

const authState = { user: localAdminUser(), logout: vi.fn() }
const authMode = { authEnabled: false }
vi.mock('../auth/AuthProvider', () => ({ useAuth: () => authState }))
vi.mock('../auth/AuthModeProvider', () => ({ useAuthMode: () => authMode }))
vi.mock('../api/projects', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/projects')>()
  return { ...actual, projectsApi: { ...actual.projectsApi, list: vi.fn(async () => []), listUnclaimed: vi.fn(async () => []) } }
})

afterEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks() })

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

// Plan F1-F, F2 (gate S8 [S5]): after a reload the home page lists the
// studies of the project the last open study lives in.
describe('the decision studies entry', () => {
  it('lists the last open study’s project’s studies', async () => {
    const ref = { project: 'site-base', studyId: 'a'.repeat(32) }
    localStorage.setItem(STUDY_REF_KEY, JSON.stringify(ref))
    useDecisionStore.setState({ active: ref })
    const list = vi.spyOn(decisionStudiesApi, 'list').mockResolvedValue(
      [{ study_id: ref.studyId, name: 'Battery at my site', maturity: { status: 'not_established' } }] as never)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><MemoryRouter><ProjectsHomePage /></MemoryRouter></QueryClientProvider>)
    expect((await screen.findByTestId('decision-studies-entry')).textContent).toContain('Battery at my site')
    expect(list).toHaveBeenCalledWith('site-base')
  })
})
