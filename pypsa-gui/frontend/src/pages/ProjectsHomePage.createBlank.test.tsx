// Guided-mode spec §3.4 (G4, literal): ProjectsHomePage.createBlank's success
// handler calls noteNewProjectCreated('blank') before it navigates into the
// workbench, so an implicit mode starts the project in Guided and an explicit
// choice is kept.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import ProjectsHomePage from './ProjectsHomePage'
import { localAdminUser } from '../auth/localMode'
import { useUIStore, type NewProjectKind } from '../store/uiStore'

const authState = { user: localAdminUser(), logout: vi.fn() }
const authMode = { authEnabled: false }

vi.mock('../auth/AuthProvider', () => ({ useAuth: () => authState }))
vi.mock('../auth/AuthModeProvider', () => ({ useAuthMode: () => authMode }))
vi.mock('../components/AssistantDock', () => ({ default: () => <div /> }))
vi.mock('../api/projects', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/projects')>()
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      list: vi.fn(async () => []),
      listUnclaimed: vi.fn(async () => []),
      save: vi.fn(async (name: string) => ({ saved: name })),
    },
  }
})
vi.mock('../api/network', () => ({
  networkApi: { resetNetwork: vi.fn(async () => ({})) },
}))
// The Blank tab only calls onConfirm(name); the stub exposes exactly that.
vi.mock('../layout/NewProjectWizard', () => ({
  default: ({ onConfirm }: { onConfirm: (name: string) => void }) => (
    <button data-testid="wizard-confirm" onClick={() => onConfirm('Fresh')}>create</button>
  ),
}))

let order: string[] = []
function Where() {
  const loc = useLocation()
  if (loc.pathname === '/app') order.push('navigate')
  return null
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/projects']}>
        <Routes>
          <Route path="*" element={<><Where /><ProjectsHomePage /></>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

async function createBlank() {
  renderPage()
  act(() => { window.dispatchEvent(new CustomEvent('chat:open-new-project-wizard')) })
  fireEvent.click(await screen.findByTestId('wizard-confirm'))
  await waitFor(() => expect(order).toContain('navigate'))
}

const realNote = useUIStore.getState().noteNewProjectCreated
const note = vi.fn((k: NewProjectKind) => { order.push(`note:${k}`); realNote(k) })

beforeEach(() => {
  order = []
  note.mockClear()
  useUIStore.setState({ noteNewProjectCreated: note, currentProject: null })
})
afterEach(() => {
  cleanup()
  useUIStore.setState({ noteNewProjectCreated: realNote })
})

describe('ProjectsHomePage.createBlank — new-project rule', () => {
  it("calls noteNewProjectCreated('blank') before navigating", async () => {
    useUIStore.setState({ uiMode: 'expert', uiModeExplicit: true })
    await createBlank()
    expect(note).toHaveBeenCalledTimes(1)
    expect(note).toHaveBeenCalledWith('blank')
    expect(order.indexOf('note:blank')).toBeLessThan(order.indexOf('navigate'))
  })

  it('an implicit Expert user lands the new project in Guided', async () => {
    useUIStore.setState({ uiMode: 'expert', uiModeExplicit: false })
    await createBlank()
    expect(useUIStore.getState().uiMode).toBe('guided')
  })

  it('an explicit Expert choice stays Expert', async () => {
    useUIStore.setState({ uiMode: 'expert', uiModeExplicit: true })
    await createBlank()
    expect(useUIStore.getState().uiMode).toBe('expert')
  })
})
