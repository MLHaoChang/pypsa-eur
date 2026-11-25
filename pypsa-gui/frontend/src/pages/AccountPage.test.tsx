import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import AccountPage, { roleLabel } from './AccountPage'
import { authApi } from '../api/auth'

vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ user: { email: 'a@example.com' } }) }))
vi.mock('../api/auth', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/auth')>()
  return { ...actual, authApi: { ...actual.authApi, account: vi.fn(), changePassword: vi.fn() } }
})

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}><MemoryRouter><AccountPage /></MemoryRouter></QueryClientProvider>,
  )
}

describe('roleLabel', () => {
  it('maps stored roles to plain words and passes unknown ones through', () => {
    expect(roleLabel('admin')).toBe('Admin')
    expect(roleLabel('member')).toBe('Member')
    expect(roleLabel('viewer')).toBe('viewer')
  })
})

describe('AccountPage', () => {
  beforeEach(() => {
    vi.mocked(authApi.account).mockResolvedValue({
      email: 'a@example.com',
      workspaces: [{ org_id: 'o1', name: 'Northwind', role: 'admin' }],
    })
    vi.mocked(authApi.changePassword).mockReset()
  })

  it('shows the read-only email and the workspace with its role', async () => {
    renderPage()
    expect(await screen.findByText('Northwind')).toBeTruthy()
    expect(screen.getByText('Admin')).toBeTruthy()
    expect(screen.getByLabelText('Email').hasAttribute('readonly')).toBe(true)
  })

  it('refuses mismatched confirmation without calling the API', async () => {
    renderPage()
    await userEvent.type(screen.getByLabelText('Current password'), 'old-password')
    await userEvent.type(screen.getByLabelText('New password'), 'new-password-1')
    await userEvent.type(screen.getByLabelText('Confirm new password'), 'new-password-2')
    await userEvent.click(screen.getByRole('button', { name: 'Change password' }))
    expect((await screen.findByRole('alert')).textContent).toContain('do not match')
    expect(authApi.changePassword).not.toHaveBeenCalled()
  })

  it('sends current and new password and confirms', async () => {
    vi.mocked(authApi.changePassword).mockResolvedValue({ ok: true })
    renderPage()
    await userEvent.type(screen.getByLabelText('Current password'), 'old-password')
    await userEvent.type(screen.getByLabelText('New password'), 'new-password-1')
    await userEvent.type(screen.getByLabelText('Confirm new password'), 'new-password-1')
    await userEvent.click(screen.getByRole('button', { name: 'Change password' }))
    await waitFor(() => expect(authApi.changePassword).toHaveBeenCalledWith({
      current_password: 'old-password', new_password: 'new-password-1',
    }))
    expect((await screen.findByRole('status')).textContent).toContain('Password changed')
  })

  it('shows the server message for a wrong current password', async () => {
    const err = Object.assign(new Error('x'), {
      isAxiosError: true, response: { status: 400, data: { detail: 'Current password is incorrect' } },
    })
    vi.mocked(authApi.changePassword).mockRejectedValue(err)
    renderPage()
    await userEvent.type(screen.getByLabelText('Current password'), 'wrong-password')
    await userEvent.type(screen.getByLabelText('New password'), 'new-password-1')
    await userEvent.type(screen.getByLabelText('Confirm new password'), 'new-password-1')
    await userEvent.click(screen.getByRole('button', { name: 'Change password' }))
    expect((await screen.findByRole('alert')).textContent).toContain('Current password is incorrect')
  })

  it('clears the success line when the user types again', async () => {
    vi.mocked(authApi.changePassword).mockResolvedValue({ ok: true })
    renderPage()
    await userEvent.type(screen.getByLabelText('Current password'), 'old-password')
    await userEvent.type(screen.getByLabelText('New password'), 'new-password-1')
    await userEvent.type(screen.getByLabelText('Confirm new password'), 'new-password-1')
    await userEvent.click(screen.getByRole('button', { name: 'Change password' }))
    await screen.findByRole('status')
    await userEvent.type(screen.getByLabelText('Current password'), 'x')
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('says so when the user has no workspace', async () => {
    vi.mocked(authApi.account).mockResolvedValue({ email: 'a@example.com', workspaces: [] })
    renderPage()
    expect(await screen.findByText(/not a member of a workspace/)).toBeTruthy()
  })
})
