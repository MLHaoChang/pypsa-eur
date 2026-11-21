// Issue 49: who may set the shared API key. Single-user (auth-off) mode keeps the
// setup offer: its synthetic local user is a super-admin.
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import { AuthProvider, useIsSuperAdmin, useSuperAdminState } from './AuthProvider'

const mode = vi.hoisted(() => ({ authEnabled: false, ready: true }))
const me = vi.hoisted(() => ({ is_super_admin: false }))
vi.mock('./AuthModeProvider', () => ({ useAuthMode: () => mode }))
vi.mock('../api/auth', () => ({
  authApi: { me: vi.fn(async () => ({ id: 'u', email: 'm@x', is_super_admin: me.is_super_admin, role: 'admin' })), logout: vi.fn() },
}))
vi.mock('./requests', () => ({ loginWithPassword: vi.fn() }))

function Probe() {
  return <p data-testid="p">{useSuperAdminState()}:{String(useIsSuperAdmin())}</p>
}
function mount() {
  return render(<QueryClientProvider client={new QueryClient()}><AuthProvider><Probe /></AuthProvider></QueryClientProvider>)
}

describe('who may set the API key', () => {
  it('auth off (single-user mode): the local user may', async () => {
    mode.authEnabled = false
    mount()
    await waitFor(() => expect(screen.getByTestId('p').textContent).toBe('yes:true'))
  })
  it('signed in as an org admin who is not a super-admin: may not', async () => {
    mode.authEnabled = true; me.is_super_admin = false
    mount()
    await waitFor(() => expect(screen.getByTestId('p').textContent).toBe('no:false'))
  })
  it('signed in as a super-admin: may', async () => {
    mode.authEnabled = true; me.is_super_admin = true
    mount()
    await waitFor(() => expect(screen.getByTestId('p').textContent).toBe('yes:true'))
  })
  it('is unknown while the user is still being read', () => {
    mode.authEnabled = true; mode.ready = false
    mount()
    expect(screen.getByTestId('p').textContent).toBe('unknown:false')
    mode.ready = true
  })
})
