// WorkspacePanel's read-only chip must name the REASON.
//
// The chip is the second dedicated read-only banner and it is NOT gated on
// `authEnabled`, so it renders in the desktop single-user build too. Reading
// `readOnly` + `lockHolderEmail` alone, it told that build "Read-only — the
// edit lock could not be acquired" while the project was merely solving in the
// queue — in a mode that has no lock machinery at all. `authEnabled: false`
// below is therefore the case under test, not a convenience.
//
// Revert the `readOnlyBannerMessage(readOnlyReason, lockHolderEmail)` wiring in
// WorkspacePanel.tsx and the first case fails.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import WorkspacePanel from './WorkspacePanel'
import { useUIStore } from '../store/uiStore'
import { projectsApi } from '../api/projects'
import { WRITABLE } from '../utils/lockState'

vi.mock('../api/projects')
vi.mock('../auth/AuthProvider', () => ({
  useAuth: () => ({ user: { email: 'solo@example.com', role: null } }),
}))
// Desktop / single-user: no auth, therefore no edit-lock machinery anywhere.
vi.mock('../auth/AuthModeProvider', () => ({
  useAuthMode: () => ({ ready: true, authEnabled: false, enableAuth: () => {} }),
}))

async function renderPanel(): Promise<string> {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><WorkspacePanel /></MemoryRouter>
    </QueryClientProvider>,
  )
  // The chip is the only element carrying "Read-only" or the writable copy.
  return await waitFor(() => {
    const chip = screen.getByText(
      (_, el) => /^(Read-only — |You hold the edit lock)/.test(el?.textContent ?? '')
        && el?.tagName === 'SPAN',
    )
    return chip.textContent ?? ''
  })
}

beforeEach(() => {
  vi.mocked(projectsApi.list).mockResolvedValue([
    {
      name: 'demo', created_at: '2026-01-01T00:00:00', has_solver_config: true,
      bus_count: 5, snapshot_count: 24, objective: null, parent_project: null,
      scenario_description: null,
    },
  ] as unknown as Awaited<ReturnType<typeof projectsApi.list>>)
  useUIStore.setState({ currentProject: 'demo', recents: [] })
  useUIStore.getState().setLockState(WRITABLE)
  useUIStore.getState().setSolvingReadOnly(false)
})

describe('WorkspacePanel read-only chip', () => {
  it('blames the queue solve, not an edit lock, in a build that has no locks', async () => {
    useUIStore.getState().setSolvingReadOnly(true)

    const text = await renderPanel()

    expect(text).toMatch(/solving in the queue/i)
    expect(text).not.toMatch(/edit lock could not be acquired/i)
  })

  it('still names the lock holder when another user holds the edit lock', async () => {
    useUIStore.getState().setLockState({
      readOnly: true, holderEmail: 'ada@example.com', reason: 'locked-by-user',
    })

    const text = await renderPanel()

    expect(text).toMatch(/ada@example\.com is currently editing this project/i)
  })

  it('says you hold the lock while writable', async () => {
    expect(await renderPanel()).toBe('You hold the edit lock')
  })
})
