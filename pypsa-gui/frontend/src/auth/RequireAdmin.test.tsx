// The admin console must be unreachable in the desktop app, not merely unlinked.
//
// Hiding the "Open admin" button (ProjectsHomePage.admin.test.tsx) closes the
// only path a user can click. This closes the ROUTE, which is the layer that
// still matters: `/admin/*` is registered in `routes.tsx` unconditionally, and
// `RequireAdmin` gated on `isAdmin` alone — which is TRUE in local mode,
// because `AuthProvider` synthesizes `localAdminUser()` with
// `is_super_admin: true` rather than `null` (deliberately — see localMode.ts).
//
// So any future link, redirect, or restored route would land a desktop user on
// an admin console whose backend does not exist: local mode never mounts
// `routers/admin.py`, and every `/api/admin/*` call 404s. Verified against the
// packaged app: `GET /api/admin/orgs -> 404`.
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import RequireAdmin from './RequireAdmin'
import { localAdminUser } from './localMode'

const authState = { isAdmin: true, status: 'authenticated' as string }
const authMode = { authEnabled: false }

vi.mock('./AuthProvider', () => ({ useAuth: () => authState }))
vi.mock('./AuthModeProvider', () => ({ useAuthMode: () => authMode }))

function renderGuard() {
  return render(
    <MemoryRouter initialEntries={['/admin']}>
      <RequireAdmin>
        <div>ADMIN CONSOLE</div>
      </RequireAdmin>
    </MemoryRouter>,
  )
}

describe('RequireAdmin', () => {
  it('refuses the admin console when auth is off, even to a synthetic admin', () => {
    // Exactly the local-mode identity: `isAdmin` is true and must not be enough.
    expect(localAdminUser().is_super_admin).toBe(true)
    authState.isAdmin = true
    authMode.authEnabled = false

    renderGuard()

    expect(screen.queryByText('ADMIN CONSOLE')).toBeNull()
  })

  it('still admits an admin on the web deployment', () => {
    authState.isAdmin = true
    authMode.authEnabled = true

    renderGuard()

    expect(screen.getByText('ADMIN CONSOLE')).toBeTruthy()
  })

  it('still refuses a non-admin on the web deployment', () => {
    authState.isAdmin = false
    authMode.authEnabled = true

    renderGuard()

    expect(screen.queryByText('ADMIN CONSOLE')).toBeNull()
  })
})
