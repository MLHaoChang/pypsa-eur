// The restore confirm modal states what a restore removes (SR-1, F02/F03): a
// snapshot with a capture record removes settings, uploads and reports added
// since it was taken (kept in "before-restore"); an older one keeps them. The
// confirm reads "Restore snapshot" and Cancel has the initial focus.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { SnapshotInfo } from '../api/projects'
import { RestoreConfirmModal } from './SnapshotsPanel'

vi.mock('../store/uiStore', () => ({
  useUIStore: (sel: (s: { currentProject: string | null }) => unknown) => sel({ currentProject: 'P' }),
}))
vi.mock('../api/network', () => ({
  networkApi: { getMeta: vi.fn(async () => ({ bus_count: 3 })), getSnapshots: vi.fn(async () => ({ count: 24 })) },
}))

const SNAP: SnapshotInfo = {
  id: 's1', label: 'v1', message: '', created_at: '2026-10-21T10:00:00+00:00',
  bus_count: 3, snapshot_count: 24, objective: null, has_results: false,
}

function renderModal(snapshot: SnapshotInfo) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <RestoreConfirmModal snapshot={snapshot} projectName="P" pending={false}
        onConfirm={() => {}} onClose={() => {}} />
    </QueryClientProvider>,
  )
}

afterEach(cleanup)

describe('RestoreConfirmModal', () => {
  it('says later files are removed and kept in before-restore', () => {
    renderModal({ ...SNAP, has_capture_manifest: true })
    expect(screen.getByText(/added since this snapshot are removed/)).toBeTruthy()
    expect(screen.queryByText(/Older snapshot/)).toBeNull()
  })

  it('says an older snapshot keeps later files', () => {
    renderModal({ ...SNAP, has_capture_manifest: false })
    expect(screen.getByText(/Older snapshot/)).toBeTruthy()
    expect(screen.getByText(/are kept as they are/)).toBeTruthy()
    expect(screen.queryByText(/added since this snapshot are removed/)).toBeNull()
  })

  it('confirms with "Restore snapshot" and focuses Cancel first', () => {
    renderModal({ ...SNAP, has_capture_manifest: true })
    expect(screen.getByRole('button', { name: 'Restore snapshot' })).toBeTruthy()
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Cancel' }))
  })
})
