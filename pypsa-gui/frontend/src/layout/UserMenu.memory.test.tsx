// UX M1: Memory opens from the account menu, and Esc returns focus to the
// menu button, however the dialog was opened.
import { afterEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import UserMenu from './UserMenu'
import { useMemoryStore } from '../store/memoryStore'

vi.mock('../api/memory', async (orig) => {
  const real = await orig<typeof import('../api/memory')>()
  return { ...real, memoryApi: { ...real.memoryApi,
    list: () => Promise.resolve({ items: [], can_promote: false }) } }
})
vi.mock('../api/auth', () => ({ authApi: { orgMembers: () => Promise.resolve([]) } }))
vi.mock('../auth/AuthProvider', () => ({
  useAuth: () => ({ user: { id: 'u1', email: 'a@example.com' }, logout: vi.fn() }),
}))

afterEach(() => { cleanup(); useMemoryStore.setState({ open: false, editId: null }) })

function renderMenu() {
  return render(<QueryClientProvider client={new QueryClient()}><UserMenu /></QueryClientProvider>)
}

it('Esc on Memory returns focus to the account menu button', async () => {
  renderMenu()
  fireEvent.click(screen.getByTestId('user-menu-button'))
  fireEvent.click(screen.getByTestId('user-menu-memory'))
  const dialog = await screen.findByRole('dialog')
  fireEvent.keyDown(dialog, { key: 'Escape' })
  await waitFor(() => expect(useMemoryStore.getState().open).toBe(false))
  expect(document.activeElement).toBe(screen.getByTestId('user-menu-button'))
})

it('also when Memory was opened from a memory card', async () => {
  renderMenu()
  act(() => useMemoryStore.getState().openMemory())
  await screen.findByRole('dialog')
  act(() => useMemoryStore.getState().closeMemory())
  expect(document.activeElement).toBe(screen.getByTestId('user-menu-button'))
})
