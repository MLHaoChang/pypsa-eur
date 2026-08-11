// Correction 1 (2026-08-08-solve-queue-full-pass, post-review pass): the
// clone wizard's onError handler read `e.message` — axios's generic
// "Request failed with status code 409" — instead of the server's typed
// `{error_kind, message}` detail. Worse, it ALWAYS appended "the in-memory
// network may now be '<source>' — reopen your original project to recover",
// which is false for a `solver_in_flight` 409: `load_project` refuses that
// case BEFORE touching anything (see routers/projects.py's
// `_queue_solve_conflict`), so there is nothing to recover from.
//
// This pins both halves: a `solver_in_flight` 409 shows the server's actual
// message and omits the recovery instruction; every OTHER clone failure mode
// keeps showing both the message and the recovery instruction, unchanged.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import toast from 'react-hot-toast'
import NewProjectWizard from './NewProjectWizard'
import { projectsApi } from '../api/projects'
import { networkApi } from '../api/network'
import type { ProjectInfo } from '../api/types'

vi.mock('../api/projects')
vi.mock('../api/network', () => ({ networkApi: { undoInfo: vi.fn() } }))

const SOURCE: ProjectInfo = {
  id: 'id-source',
  name: 'source',
  created_at: '2026-01-01T00:00:00',
  has_solver_config: true,
  bus_count: 5,
  snapshot_count: 24,
  objective: null,
}

function renderWizard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <NewProjectWizard
        existingProjects={[SOURCE]}
        onConfirm={() => {}}
        onClose={() => {}}
        isPending={false}
        initialTab="clone"
      />
    </QueryClientProvider>,
  )
}

// Picks the source project and fires Clone. `newName` autofills to
// `${sourceId}_copy` the first time a source is picked (CloneTab's own
// effect), so no typing is required to reach a clonable state.
async function driveClone() {
  renderWizard()
  await userEvent.click(await screen.findByRole('button', { name: /^source/ }))
  const cloneBtn = await screen.findByRole('button', { name: 'Clone project' })
  await userEvent.click(cloneBtn)
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(projectsApi.list).mockResolvedValue([SOURCE])
  vi.mocked(networkApi.undoInfo).mockResolvedValue({ depth: 0 })
  vi.spyOn(toast, 'error').mockImplementation(() => '')
  vi.spyOn(toast, 'success').mockImplementation(() => '')
})

describe('CloneTab onError — solver_in_flight refusal', () => {
  it('shows the server message and NOT the false recovery instruction', async () => {
    vi.mocked(projectsApi.load).mockRejectedValueOnce({
      message: 'Request failed with status code 409',
      response: {
        status: 409,
        data: {
          detail: {
            error_kind: 'solver_in_flight',
            message: "'source' is being solved by the queue right now. Wait for the job to finish, or abort it, then reopen the project.",
          },
        },
      },
    })

    await driveClone()

    await waitFor(() => expect(vi.mocked(toast.error)).toHaveBeenCalled())
    const shown = String(vi.mocked(toast.error).mock.calls[0][0])

    // The server's actionable message reached the user...
    expect(shown).toContain('is being solved by the queue right now')
    // ...not axios's generic status-code string...
    expect(shown).not.toContain('Request failed with status code')
    // ...and the refusal happens before anything is mutated, so there is
    // nothing to "reopen and recover" — that instruction must be absent.
    expect(shown).not.toContain('reopen your original project to recover')
  })

  it('other clone failures keep the message AND the recovery instruction', async () => {
    vi.mocked(projectsApi.load).mockResolvedValueOnce({
      buses: 0, generators: 0, lines: 0, links: 0, storage_units: 0,
      stores: 0, loads: 0, transformers: 0, snapshots: 0,
    } as never)
    vi.mocked(projectsApi.save).mockRejectedValueOnce({
      message: 'Request failed with status code 500',
      response: { status: 500, data: { detail: 'disk full' } },
    })

    await driveClone()

    await waitFor(() => expect(vi.mocked(toast.error)).toHaveBeenCalled())
    const shown = String(vi.mocked(toast.error).mock.calls[0][0])

    expect(shown).toContain('disk full')
    expect(shown).toContain("reopen your original project to recover")
  })
})
