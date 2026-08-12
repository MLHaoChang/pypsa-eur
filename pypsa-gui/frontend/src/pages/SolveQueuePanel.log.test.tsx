// R20 — the expand control shows the LIVE log for a running row and the
// RETAINED log for a terminal row, for all four terminal statuses.
//
// Before this, expand was enabled only for `completed` and only ever showed the
// results bundle: a failed job's output was unreachable from the panel, and a
// running job's log could only be read by being on the project that owned it.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { SolveJob, SolveJobStatus } from '../api/solveQueue'
import SolveQueuePanel, { canExpandJob } from './SolveQueuePanel'

function job(status: SolveJobStatus, project_id: string | null = 'demo'): SolveJob {
  return {
    id: 5, project_id, project_key: null, status,
    position: null, objective: null, solve_time: null, condition: null, error: null,
    enqueued_at: 0, started_at: 0, finished_at: 1,
  }
}

let jobs: SolveJob[] = []
const jobLogHistory = vi.fn()

vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ user: null }) }))
vi.mock('../store/uiStore', () => ({
  useUIStore: () => ({ currentProject: null, openTabs: [], markProjectSaved: vi.fn() }),
}))
vi.mock('../hooks/useSolveQueue', () => ({
  useSolveQueue: () => ({ data: { jobs, current: null }, isLoading: false, isError: false }),
  useEnqueueSolve: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useAbortJob: () => ({ mutate: vi.fn(), isPending: false }),
  useClearFinished: () => ({ mutate: vi.fn(), isPending: false }),
}))
vi.mock('../api/solveQueue', async (orig) => ({
  ...(await orig<typeof import('../api/solveQueue')>()),
  solveQueueApi: {
    ...(await orig<typeof import('../api/solveQueue')>()).solveQueueApi,
    jobLogHistory: (id: number) => jobLogHistory(id),
    jobLogStreamUrl: (id: number) => `/api/simulation/queue/${id}/log_stream`,
    resultsBundle: vi.fn().mockResolvedValue(null),
  },
}))

afterEach(() => cleanup())
beforeEach(() => { jobs = []; jobLogHistory.mockReset() })

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <SolveQueuePanel />
    </QueryClientProvider>,
  )
}

describe('canExpandJob', () => {
  it('is false for a queued job — it has produced nothing yet', () => {
    expect(canExpandJob(job('queued'))).toBe(false)
  })

  it('is true for a running job', () => {
    expect(canExpandJob(job('running'))).toBe(true)
  })

  it('is true for every terminal status, interrupted included', () => {
    for (const s of ['completed', 'failed', 'aborted', 'interrupted'] as const) {
      expect(canExpandJob(job(s as SolveJobStatus))).toBe(true)
    }
  })

  it('is false for a redacted row whatever its status', () => {
    expect(canExpandJob(job('completed', null))).toBe(false)
  })
})

describe('SolveQueuePanel log expansion', () => {
  it('shows the retained log when a terminal row is expanded', async () => {
    jobs = [job('failed')]
    jobLogHistory.mockResolvedValue({ lines: ['solver: infeasible'], status: 'failed' })
    renderPanel()
    await userEvent.click(screen.getByTitle('Show this job’s log'))
    await waitFor(() => expect(screen.getByText('solver: infeasible')).toBeTruthy())
    expect(jobLogHistory).toHaveBeenCalledWith(5)
  })
})
