// Increment 3 shipped five queue routes API-only — pause, resume,
// cancel_queued, {id}/requeue, {id}/dismiss — plus a `paused` field the
// listing returned and nothing rendered. These tests pin the client surface.
//
// Two rules run through all of them, both inherited from the Clear finished
// gate this file sits beside:
//
//  1. A control must match its route exactly. Rendering something enabled that
//     the server will refuse is worse than not rendering it: the user gets an
//     error for a thing the UI offered them. Pause/Resume therefore read the
//     RAW `is_super_admin` (never `useAuth().isAdmin`, which is also true for
//     an org admin and would guarantee a 403), and Dismiss reads the row's
//     server-computed `can_dismiss` rather than inferring from the status.
//  2. Disabled with an explanatory `title`, not hidden. A missing button is
//     indistinguishable from a broken one.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { QueueList, SolveJob } from '../api/solveQueue'
import { localAdminUser } from '../auth/localMode'
import SolveQueuePanel from './SolveQueuePanel'

function job(over: Partial<SolveJob> = {}): SolveJob {
  return {
    id: '11111111-1111-4111-8111-111111111111',
    project_id: 'demo',
    project_key: 'org:demo',
    status: 'completed',
    position: null,
    objective: 1,
    solve_time: 1,
    condition: 'optimal',
    error: null,
    enqueued_at: 0,
    started_at: 0,
    finished_at: 1,
    can_dismiss: true,
    ...over,
  }
}

let authState: { user: { is_super_admin: boolean; role: string | null } | null }
let queueData: QueueList
const pauseMutate = vi.fn()
const resumeMutate = vi.fn()
const cancelQueuedMutate = vi.fn()
const requeueMutate = vi.fn()
const dismissMutate = vi.fn()

vi.mock('../auth/AuthProvider', () => ({ useAuth: () => authState }))

vi.mock('../store/uiStore', () => ({
  useUIStore: () => ({ currentProject: null, openTabs: [], markProjectSaved: vi.fn() }),
}))

vi.mock('../hooks/useSolveQueue', () => ({
  useSolveQueue: () => ({ data: queueData, isLoading: false, isError: false }),
  useEnqueueSolve: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
  useAbortJob: () => ({ mutate: vi.fn(), isPending: false }),
  useClearFinished: () => ({ mutate: vi.fn(), isPending: false }),
  usePauseQueue: () => ({ mutate: pauseMutate, isPending: false }),
  useResumeQueue: () => ({ mutate: resumeMutate, isPending: false }),
  useCancelQueued: () => ({ mutate: cancelQueuedMutate, isPending: false }),
  useRequeueJob: () => ({ mutate: requeueMutate, isPending: false }),
  useDismissJob: () => ({ mutate: dismissMutate, isPending: false }),
}))

afterEach(() => cleanup())
beforeEach(() => {
  authState = { user: { is_super_admin: true, role: 'admin' } }
  queueData = { jobs: [], running: [], paused: false }
  vi.clearAllMocks()
})

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <SolveQueuePanel />
    </QueryClientProvider>,
  )
}

const btn = (name: RegExp) => screen.getByRole('button', { name }) as HTMLButtonElement
const maybeBtn = (name: RegExp) => screen.queryByRole('button', { name }) as HTMLButtonElement | null

describe('the paused state is visible', () => {
  it('says nothing while the dispatcher is running', () => {
    renderPanel()
    expect(screen.queryByText(/paused/i)).toBeNull()
  })

  it('announces the pause, and what it does NOT do', () => {
    // The distinction is the whole point: pausing starts no NEW jobs, and a
    // job already solving runs to completion. A banner saying only "Paused"
    // would read as "everything stopped", and a user watching a long solve
    // continue would reasonably conclude the pause had failed.
    queueData = { jobs: [job({ status: 'running' })], running: [job().id], paused: true }
    renderPanel()
    expect(screen.getByText(/paused/i)).toBeTruthy()
    expect(screen.getByText(/running jobs finish/i)).toBeTruthy()
  })
})

describe('Pause / Resume — instance-wide, super-admin gated', () => {
  it('offers Pause while running and Resume while paused, never both', () => {
    renderPanel()
    expect(maybeBtn(/^pause queue$/i)).not.toBeNull()
    expect(maybeBtn(/^resume queue$/i)).toBeNull()

    cleanup()
    queueData = { jobs: [], running: [], paused: true }
    renderPanel()
    expect(maybeBtn(/^resume queue$/i)).not.toBeNull()
    expect(maybeBtn(/^pause queue$/i)).toBeNull()
  })

  it('is disabled with a reason for an ORG admin — isAdmin would have enabled it', () => {
    // hasAdminConsoleAccess({role:'admin'}) is true, so a gate on
    // useAuth().isAdmin enables this button and then 403s. Same trap the
    // Clear finished tests pin; repeated here because it is a NEW control
    // making the same choice, not a re-test of the old one.
    authState = { user: { is_super_admin: false, role: 'admin' } }
    renderPanel()
    const b = btn(/^pause queue$/i)
    expect(b.disabled).toBe(true)
    expect(b.title).toMatch(/super-admin/i)
  })

  it('is enabled in local mode — the packaged desktop app must not regress', () => {
    const local = localAdminUser()
    authState = { user: { is_super_admin: local.is_super_admin, role: local.role } }
    renderPanel()
    expect(btn(/^pause queue$/i).disabled).toBe(false)
  })

  it('calls the pause mutation', () => {
    renderPanel()
    fireEvent.click(btn(/^pause queue$/i))
    expect(pauseMutate).toHaveBeenCalledTimes(1)
  })
})

describe('Cancel queued', () => {
  it('is disabled when nothing is queued', () => {
    queueData = { jobs: [job({ status: 'running' })], running: [job().id], paused: false }
    renderPanel()
    expect(btn(/cancel queued/i).disabled).toBe(true)
  })

  it('is enabled when there is a queued job, and needs no super-admin', () => {
    // Deliberately NOT super-admin gated server-side: it sweeps only what the
    // caller could have cancelled one at a time. Pinning that here stops a
    // future "make it consistent with Clear finished" from silently removing
    // the ability from every ordinary user.
    authState = { user: { is_super_admin: false, role: 'member' } }
    queueData = { jobs: [job({ status: 'queued', position: 1 })], running: [], paused: false }
    renderPanel()
    expect(btn(/cancel queued/i).disabled).toBe(false)
  })
})

describe('per-row Requeue', () => {
  it('is offered on a finished row and calls the mutation with the job id', () => {
    queueData = { jobs: [job({ status: 'completed' })], running: [], paused: false }
    renderPanel()
    fireEvent.click(btn(/run again/i))
    expect(requeueMutate).toHaveBeenCalledTimes(1)
    expect(requeueMutate.mock.calls[0][0]).toBe(job().id)
  })

  it('is offered on an INTERRUPTED row', () => {
    // R25 bars only AUTOMATIC re-enqueue at boot — the crash-loop guard. A
    // user clicking "run it again" is not that, and an interrupted job is
    // precisely the one a user most wants to restart.
    queueData = { jobs: [job({ status: 'interrupted' })], running: [], paused: false }
    renderPanel()
    expect(maybeBtn(/run again/i)).not.toBeNull()
  })

  it('is NOT offered on a queued or running row', () => {
    for (const status of ['queued', 'running'] as const) {
      cleanup()
      queueData = { jobs: [job({ status })], running: [], paused: false }
      renderPanel()
      expect(maybeBtn(/run again/i)).toBeNull()
    }
  })

  it('is NOT offered on a redacted row', () => {
    // A row the caller may not see 404s on every job endpoint, so the control
    // would fail every time it was clicked.
    queueData = {
      jobs: [job({ project_id: null, project_key: null, can_dismiss: false })],
      running: [], paused: false,
    }
    renderPanel()
    expect(maybeBtn(/run again/i)).toBeNull()
  })
})

describe('per-row Dismiss', () => {
  it('is offered when the server says the row is dismissible', () => {
    queueData = { jobs: [job({ can_dismiss: true })], running: [], paused: false }
    renderPanel()
    fireEvent.click(btn(/dismiss/i))
    expect(dismissMutate).toHaveBeenCalledTimes(1)
    expect(dismissMutate.mock.calls[0][0]).toBe(job().id)
  })

  it('is NOT offered on a terminal row the caller did not queue', () => {
    // THE LOAD-BEARING CASE. The row is terminal and fully visible, so any
    // status-derived condition (`isTerminal(job)`) renders the button — and
    // the route 403s because dismissal is owner-gated. Only reading the
    // server's `can_dismiss` gets this right, which is exactly why the field
    // exists.
    queueData = { jobs: [job({ status: 'completed', can_dismiss: false })], running: [], paused: false }
    renderPanel()
    expect(maybeBtn(/dismiss/i)).toBeNull()
  })
})
