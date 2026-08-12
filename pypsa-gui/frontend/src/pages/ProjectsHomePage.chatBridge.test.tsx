import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import ProjectsHomePage from './ProjectsHomePage'
import { localAdminUser } from '../auth/localMode'
import { useUIStore } from '../store/uiStore'

// The chat → app event bridge, on the page that has no Sidebar.
//
// The assistant's no-project greeting offers "Open project" and "New project",
// and both work by dispatching a CustomEvent rather than reaching across the
// layout tree — a deliberate decoupling documented where the buttons were
// first written. The listener, however, lives in Sidebar's
// ProjectSectionContent.
//
// Sidebar does not render at `/projects`. So the moment the dock arrived on
// the landing page, those two buttons became dead controls THERE and nowhere
// else — a failure that no existing test could see, because every test of the
// bridge renders Sidebar. Two dead buttons on the front door is worse than the
// missing assistant they were added to fix.
//
// The picker event has no picker to open here: this page IS the project list.
// It resolves to bringing that list into view, which is the honest local
// meaning of "show me my projects".

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

beforeEach(() => {
  useUIStore.setState({ assistantDockOpen: true, currentProject: null })
})

afterEach(() => {
  cleanup()
  authState.user = localAdminUser()
  authMode.authEnabled = false
})

describe('the assistant’s project buttons on the landing page', () => {
  it('opens the new-project wizard', async () => {
    renderPage()
    await screen.findByRole('navigation', { name: 'Account' })

    fireEvent.click(screen.getByTestId('chat-empty-new-project'))

    expect(await screen.findByTestId('new-project-wizard')).toBeTruthy()
  })

  it('brings the project list into view for the picker', async () => {
    renderPage()
    await screen.findByRole('navigation', { name: 'Account' })

    const heading = document.getElementById('projects-heading')!
    const scrollIntoView = vi.fn()
    heading.scrollIntoView = scrollIntoView

    fireEvent.click(screen.getByTestId('chat-empty-open-project'))

    expect(scrollIntoView).toHaveBeenCalled()
  })

  // The listener must not outlive the page, or a later dispatch from the
  // workbench opens a wizard belonging to an unmounted tree.
  it('stops listening once the page unmounts', async () => {
    const { unmount } = renderPage()
    await screen.findByRole('navigation', { name: 'Account' })
    unmount()

    act(() => {
      window.dispatchEvent(new CustomEvent('chat:open-new-project-wizard'))
    })

    expect(screen.queryByTestId('new-project-wizard')).toBeNull()
  })
})
