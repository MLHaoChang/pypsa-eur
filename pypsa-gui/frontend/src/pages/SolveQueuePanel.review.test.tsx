// 2026-08-14 adversarial review — panel robustness and honest feedback.
//
// Four defects pinned here:
//  1. `STATUS_META[status]` crashed the WHOLE panel on a status outside the
//     six known ones. `db/models.py` documents that `solve_jobs.status` is a
//     plain string precisely so "an unknown value must degrade rather than
//     break the row" — the row degrades; the panel must too.
//  2. Abort on a row the caller may not see 404s by design (the byte-identical
//     existence-oracle 404). The toast surfaced raw axios text
//     ("Request failed with status code 404") instead of saying what happened.
//  3. A duplicate enqueue returns the EXISTING job with `already_queued: true`
//     (idempotent 200, routers/solve_queue.py) — the panel discarded the flag
//     and toasted "Queued 'X' to solve" for a no-op.
//  4. `clearFinished.mutate()` had no onError: a 403 (super-admin revoked
//     since page load) or any failure did nothing visible at all.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { SolveJob, SolveJobStatus } from '../api/solveQueue'
import SolveQueuePanel from './SolveQueuePanel'

const toastMock = vi.hoisted(() => {
  const base = vi.fn() as ReturnType<typeof vi.fn> & {
    success: ReturnType<typeof vi.fn>
    error: ReturnType<typeof vi.fn>
  }
  base.success = vi.fn()
  base.error = vi.fn()
  return base
})
vi.mock('react-hot-toast', () => ({ default: toastMock }))

function job(status: SolveJobStatus | string, project_id: string | null = 'demo'): SolveJob {
  return {
    id: '77777777-7777-4777-8777-777777777777',
    project_id, project_key: project_id ? `org:${project_id}` : null,
    status: status as SolveJobStatus,
    position: null, objective: null, solve_time: null, condition: null, error: null,
    enqueued_at: 0, started_at: 0, finished_at: null,
    // Not the subject of this file; false keeps the Dismiss control out of
    // the DOM so it cannot interfere with the queries below.
    can_dismiss: false,
  }
}

let jobs: SolveJob[] = []
let currentProject: string | null = null
const abortMutate = vi.fn()
const clearMutate = vi.fn()
const enqueueMutateAsync = vi.fn()
let authState: { user: { is_super_admin: boolean } | null } = { user: null }

vi.mock('../auth/AuthProvider', () => ({ useAuth: () => authState }))
vi.mock('../store/uiStore', () => ({
  useUIStore: () => ({ currentProject, openTabs: [], markProjectSaved: vi.fn() }),
}))
vi.mock('../api/projects', () => ({
  projectsApi: { save: vi.fn().mockResolvedValue({}) },
}))
vi.mock('../hooks/useSolveQueue', () => ({
  useSolveQueue: () => ({ data: { jobs, running: [], paused: false }, isLoading: false, isError: false }),
  useEnqueueSolve: () => ({ mutateAsync: enqueueMutateAsync, isPending: false }),
  useAbortJob: () => ({ mutate: abortMutate, isPending: false }),
  useClearFinished: () => ({ mutate: clearMutate, isPending: false }),
  // Increment 3's five routes. Stubbed here because this file mocks the hook
  // module WHOLESALE — an absent export is `undefined` at the call site, so the
  // panel throws on render and every test in the file fails for a reason that
  // has nothing to do with what it asserts.
  usePauseQueue: () => ({ mutate: vi.fn(), isPending: false }),
  useResumeQueue: () => ({ mutate: vi.fn(), isPending: false }),
  useCancelQueued: () => ({ mutate: vi.fn(), isPending: false }),
  useRequeueJob: () => ({ mutate: vi.fn(), isPending: false }),
  useDismissJob: () => ({ mutate: vi.fn(), isPending: false }),
}))

afterEach(() => cleanup())
beforeEach(() => {
  jobs = []
  currentProject = null
  authState = { user: null }
  abortMutate.mockReset()
  clearMutate.mockReset()
  enqueueMutateAsync.mockReset()
  toastMock.mockReset()
  toastMock.success.mockReset()
  toastMock.error.mockReset()
})

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <SolveQueuePanel />
    </QueryClientProvider>,
  )
}

describe('unknown job status', () => {
  it('renders the row with the raw status instead of crashing the panel', () => {
    // A row written by a newer backend (or a legacy value) must degrade to
    // its raw status string — one odd badge, not a blank panel for ALL rows.
    jobs = [job('paused'), job('running', 'other')]
    renderPanel()
    expect(screen.getByText('paused')).toBeTruthy()
    // The other, well-formed row is unaffected.
    expect(screen.getByText('other')).toBeTruthy()
  })
})

describe('abort feedback', () => {
  it('explains a 404 instead of echoing raw axios text', async () => {
    jobs = [job('running', null)] // redacted row — abort will 404 by design
    abortMutate.mockImplementation((_id: string, opts?: { onError?: (e: unknown) => void }) => {
      opts?.onError?.(Object.assign(new Error('Request failed with status code 404'), {
        response: { status: 404, data: { detail: 'No solve job with id x.' } },
      }))
    })
    renderPanel()
    await userEvent.click(screen.getByTitle('Abort this solve'))
    await waitFor(() => expect(toastMock.error).toHaveBeenCalled())
    const msg = String(toastMock.error.mock.calls[0][0])
    expect(msg).not.toContain('Request failed with status code 404')
    expect(msg.toLowerCase()).toContain('not visible to your account')
  })
})

describe('idempotent enqueue feedback', () => {
  it("says the project is already queued instead of claiming a new job was created", async () => {
    currentProject = 'demo'
    enqueueMutateAsync.mockResolvedValue({ ...job('queued'), already_queued: true })
    renderPanel()
    await userEvent.click(screen.getByText('Queue current project'))
    await waitFor(() => expect(toastMock.success).toHaveBeenCalled())
    const msg = String(toastMock.success.mock.calls[0][0])
    expect(msg).toContain('already')
    expect(msg).not.toBe("Queued 'demo' to solve")
  })
})

describe('clear finished feedback', () => {
  it('surfaces a failure instead of doing nothing visible', async () => {
    authState = { user: { is_super_admin: true } }
    jobs = [job('completed')]
    clearMutate.mockImplementation((_arg?: unknown, opts?: { onError?: (e: unknown) => void }) => {
      opts?.onError?.(Object.assign(new Error('Request failed with status code 403'), {
        response: { status: 403, data: { detail: 'Clearing finished jobs is restricted.' } },
      }))
    })
    renderPanel()
    await userEvent.click(screen.getByText('Clear finished'))
    await waitFor(() => expect(toastMock.error).toHaveBeenCalled())
    expect(String(toastMock.error.mock.calls[0][0])).toContain('Clearing finished jobs is restricted.')
  })
})
