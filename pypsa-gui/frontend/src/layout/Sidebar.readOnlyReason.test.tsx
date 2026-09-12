// R11 wiring — fix round 1 follow-up.
//
// A code-review round found the first pass's reachability argument false for
// this file: Sidebar's "Save" row is a plain `SItem` (src/layout/Sidebar.tsx,
// the `SItem` definition takes no `disabled` prop at all), so clicking it
// while the project is read-only is NOT blocked by any disabled attribute —
// unlike ScenariosPanel's row buttons, which genuinely are. `handleSave` ->
// `saveAndExportBundle` -> `guardProjectMutation({silent:false})` is a real,
// ordinary-click-reachable path, and is arguably the single most likely way a
// real user meets this message. This pins it directly.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import toast from 'react-hot-toast'
import Sidebar from './Sidebar'
import { useUIStore } from '../store/uiStore'
import { WRITABLE } from '../utils/lockState'
import { READ_ONLY_MUTATION_MESSAGE, SOLVING_MUTATION_MESSAGE } from '../utils/mutationGuard'

vi.mock('../hooks/useSolveQueue', () => ({
  useSolveQueue: () => ({ data: { jobs: [], running: [], paused: false } }),
}))
// The Settings row's availability check hits /api/local-settings with
// retry:2 hardcoded on the query itself (overrides the QueryClient's
// retry:false default) — not what's under test here, and its backoff just
// adds latency. Stub it out the same way the panel does for a non-desktop
// build (routes 404 -> hides the row).
vi.mock('../hooks/useLocalSettings', () => ({
  useLocalSettingsAvailable: () => false,
}))

function renderSidebar() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><Sidebar /></MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  // `vi.spyOn` on an already-spied method does not clear prior call history —
  // without this, a toast.error call from an EARLIER test survives into a
  // later test's `.not.toHaveBeenCalledWith(...)` check and fails it for the
  // wrong reason. Same reasoning as OverviewPanel.download.test.tsx's note on
  // `vi.restoreAllMocks()` not resetting `vi.fn()` call counts.
  vi.clearAllMocks()
  useUIStore.setState({ currentProject: 'demo', projectName: 'demo', sidebarMode: 'expanded' })
  useUIStore.getState().setLockState(WRITABLE)
  useUIStore.getState().setSolvingReadOnly(false)
  vi.spyOn(toast, 'error').mockImplementation(() => '')
  vi.spyOn(toast, 'success').mockImplementation(() => '')
})

describe("Sidebar's Save action names the SOLVING reason, not the edit-lock one", () => {
  it('clicking Save while the project solves shows the solving message', async () => {
    useUIStore.getState().setSolvingReadOnly(true)
    renderSidebar()

    await userEvent.click(screen.getByRole('button', { name: 'Save' }))

    expect(vi.mocked(toast.error)).toHaveBeenCalledWith(SOLVING_MUTATION_MESSAGE)
    expect(vi.mocked(toast.error)).not.toHaveBeenCalledWith(READ_ONLY_MUTATION_MESSAGE)
  })

  it('still shows the edit-lock message when that is the actual reason (no solve involved)', async () => {
    useUIStore.getState().setLockState({
      readOnly: true, holderEmail: 'other@example.com', reason: 'locked-by-user',
    })
    renderSidebar()

    await userEvent.click(screen.getByRole('button', { name: 'Save' }))

    expect(vi.mocked(toast.error)).toHaveBeenCalledWith(READ_ONLY_MUTATION_MESSAGE)
    expect(vi.mocked(toast.error)).not.toHaveBeenCalledWith(SOLVING_MUTATION_MESSAGE)
  })

  it('saves normally (no toast) while writable', async () => {
    renderSidebar()

    await userEvent.click(screen.getByRole('button', { name: 'Save' }))

    expect(vi.mocked(toast.error)).not.toHaveBeenCalledWith(SOLVING_MUTATION_MESSAGE)
    expect(vi.mocked(toast.error)).not.toHaveBeenCalledWith(READ_ONLY_MUTATION_MESSAGE)
  })
})
