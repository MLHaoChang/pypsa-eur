import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import ProjectsHomePage from './ProjectsHomePage'
import { localAdminUser } from '../auth/localMode'
import { useUIStore } from '../store/uiStore'

// The assistant on the front door.
//
// `/projects` is where the app OPENS (routes.tsx sends `/` there), and the
// assistant did not exist on it at all — the dock is mounted by App.tsx, which
// only renders at `/app`. So the first screen of every session was the one
// screen with no assistant, and the user reported exactly that: "not
// integrated into the app landing page".
//
// The design spec asks for this directly: "With no project open, the greeting
// says so and offers the two useful next actions — open a recent project, or
// create one. This makes the assistant the natural entry point on a cold start
// rather than an empty canvas." That greeting has nowhere to render until the
// dock is on this page.
//
// ChatPanel is stubbed: the subject here is placement, and the panel's own
// no-project behaviour (CHAT_STARTER_PROMPTS_UNBOUND, the `!currentProject`
// guards on the history and uploads effects) is covered by its own suite.

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
vi.mock('../components/ChatPanel', () => ({
  default: () => <div data-testid="chat-panel-stub" />,
}))

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

describe('the assistant on the projects home', () => {
  it('is mounted on the landing page', async () => {
    renderPage()
    await screen.findByRole('navigation', { name: 'Account' })

    expect(screen.getByTestId('assistant-dock')).toBeTruthy()
  })

  // Collapsed is a legitimate state — the user's choice persists across
  // surfaces — but the strip has to still be there, or the landing page loses
  // the affordance entirely the first time someone collapses it in the
  // workbench.
  it('keeps its launcher strip when the user has collapsed it', async () => {
    useUIStore.setState({ assistantDockOpen: false })
    renderPage()
    await screen.findByRole('navigation', { name: 'Account' })

    expect(screen.getByTestId('assistant-dock')).toBeTruthy()
    expect(screen.getByTestId('assistant-dock-launcher')).toBeTruthy()
  })

  // The page is `data-pypsa-surface="brand-dark"` on purpose (see its own
  // comment: the projects home is always the dark front door, and token-driven
  // children must follow it rather than the user's workbench theme). The dock
  // is a token-driven child, so it has to sit INSIDE that subtree — mounting
  // it as a sibling would render a light-themed panel against the dark page
  // for anyone whose workbench preference is light.
  it('renders inside the brand-dark token surface', async () => {
    renderPage()
    await screen.findByRole('navigation', { name: 'Account' })

    const surface = document.querySelector('[data-pypsa-surface="brand-dark"]')
    expect(surface).toBeTruthy()
    expect(surface!.contains(screen.getByTestId('assistant-dock'))).toBe(true)
  })
})
