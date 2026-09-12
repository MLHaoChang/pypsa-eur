import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import CommandPalette from './CommandPalette'
import { useUIStore } from '../store/uiStore'
import { projectsApi } from '../api/projects'

// Restoring a snapshot REPLACES the in-memory network — it is destructive in
// exactly the sense project-delete and a destructive import are, and it was
// the only one of the three reachable in a single keystroke from the palette
// with no confirmation at all.
vi.mock('../api/projects')
vi.mock('../api/localSettings', async (orig) => ({
  ...(await orig<typeof import('../api/localSettings')>()),
  fetchLocalSettings: vi.fn().mockResolvedValue(null),
}))
vi.mock('react-hot-toast', () => ({
  default: { loading: vi.fn(() => 't1'), success: vi.fn(), error: vi.fn() },
}))

function renderPalette() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  useUIStore.setState({ paletteMode: 'all', currentProject: 'Alpha', projectName: 'Alpha' })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <CommandPalette />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(projectsApi.listSnapshots).mockResolvedValue([
    { id: 'snap-1', label: 'Before tuning', bus_count: 3, snapshot_count: 24,
      has_results: true, created_at: new Date().toISOString() },
  ] as never)
  vi.mocked(projectsApi.restoreSnapshot).mockResolvedValue({} as never)
  vi.mocked(projectsApi.list).mockResolvedValue([] as never)
})

async function findSnapshotRow() {
  return await screen.findByText('Before tuning')
}

describe('palette snapshot restore is confirmed', () => {
  it('asks before overwriting the network, and restores only on confirm', async () => {
    renderPalette()
    await userEvent.click(await findSnapshotRow())

    expect(projectsApi.restoreSnapshot).not.toHaveBeenCalled()

    const dialog = await screen.findByRole('dialog')
    expect(dialog.textContent).toMatch(/replace|overwrit|unsaved/i)

    await userEvent.click(screen.getByRole('button', { name: /restore/i }))
    await waitFor(() =>
      expect(projectsApi.restoreSnapshot).toHaveBeenCalledWith('Alpha', 'snap-1'))
  })

  it('does not restore when the confirm is cancelled', async () => {
    renderPalette()
    await userEvent.click(await findSnapshotRow())
    await screen.findByRole('dialog')

    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() =>
      expect(screen.queryByRole('dialog')).toBeNull())
    expect(projectsApi.restoreSnapshot).not.toHaveBeenCalled()
  })
})
