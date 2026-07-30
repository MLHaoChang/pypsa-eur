// The "Open admin" entry point must not exist in the desktop app.
//
// Found by looking at the packaged macOS app on screen: the button renders
// top-right, and `GET /api/admin/orgs` returns 404 because local mode does not
// mount `routers/admin.py` at all. Spec workstream B item 5 said to hide
// sign-out and the user menu; this affordance was missed, so the first thing a
// desktop user can click that looks like a feature is a dead end.
//
// The Sign-out button four lines below it already carries both the guard and
// the reasoning — "With auth off, logout() is a no-op and
// redirectAfterLogout() lands back here — the button just appeared broken."
// Same conclusion, same fix, one element over.
//
// BOTH directions are asserted. A test that only checks the local case passes
// against a page that deleted the link outright, which would take the admin
// console away from the web deployment that still needs it.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
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
  return {
    ...actual,
    projectsApi: { ...actual.projectsApi, list: vi.fn(async () => []), listUnclaimed: vi.fn(async () => []) },
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

afterEach(() => {
  authState.user = localAdminUser()
  authMode.authEnabled = false
})

describe('the import-a-folder entry point', () => {
  it('is offered in the desktop app, where it is the only way in', async () => {
    // The packaged app cannot reach a pre-desktop tree: `resolve_legacy_root()`
    // finds no `backend/projects` in a bundle, and the shell CLEARS
    // `PYPSAGUI_LEGACY_IMPORT_ROOT` on every frozen launch. Without this card a
    // user who installed the app has no route in but one file at a time.
    authMode.authEnabled = false

    renderPage()

    expect(await screen.findByText('Import a folder')).toBeTruthy()
  })

  it('is absent on the web deployment, where the route 404s', async () => {
    // It takes a SERVER-SIDE path and copies what it finds, so it is refused
    // when auth is on. Offering it there would be another "Open admin".
    authMode.authEnabled = true

    renderPage()

    await screen.findByRole('navigation', { name: 'Account' })
    expect(screen.queryByText('Import a folder')).toBeNull()
  })
})

describe('the admin console entry point', () => {
  it('is absent in the desktop app, where /api/admin/* is not mounted', async () => {
    // `localAdminUser()` sets `is_super_admin: true` and `role: 'admin'`, so
    // `hasAdminConsoleAccess(user)` is TRUE here. That is exactly why gating on
    // it alone was not enough — the synthesized local identity passes the very
    // check that was supposed to hide the button.
    authMode.authEnabled = false

    renderPage()

    // Scoped to the account nav: "New project" also appears as a wizard card,
    // and `findByText` matched both.
    const nav = await screen.findByRole('navigation', { name: 'Account' })
    expect(within(nav).queryByText('Open admin')).toBeNull()
  })

  it('is still offered to an admin on the web deployment', async () => {
    authMode.authEnabled = true

    renderPage()

    const nav = await screen.findByRole('navigation', { name: 'Account' })
    expect(within(nav).getByText('Open admin')).toBeTruthy()
  })

  it('is not offered to a non-admin even with auth on', async () => {
    authMode.authEnabled = true
    authState.user = { ...localAdminUser(), is_super_admin: false, role: 'member' }

    renderPage()

    const nav = await screen.findByRole('navigation', { name: 'Account' })
    expect(within(nav).queryByText('Open admin')).toBeNull()
  })
})
