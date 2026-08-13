// R13 — a REDACTED row must render a fixed, non-empty label and must never be
// expandable.
//
// The backend nulls `project_id`, `project_key` and `error` for a job the
// caller may not see (routers/solve_queue.py `_REDACTED`), and the frontend
// type declared `project_id: string` and omitted `project_key` entirely. The
// row therefore rendered a blank name, and expanding a redacted COMPLETED row
// would have fetched `/projects/null/results_bundle`.
//
// R12 — and the help copy must not claim the editor is busy while the queue
// runs, which `routers/projects.py:1937-1941` exists precisely to make false.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { SolveJob } from '../api/solveQueue'
import SolveQueuePanel, { REDACTED_PROJECT_LABEL } from './SolveQueuePanel'

const redactedJob: SolveJob = {
  id: 7,
  project_id: null,
  project_key: null,
  status: 'completed',
  position: null,
  objective: null,
  solve_time: null,
  condition: null,
  error: null,
  enqueued_at: 0,
  started_at: 0,
  finished_at: 1,
}

const runningJob: SolveJob = { ...redactedJob, id: 8, project_id: 'mine', status: 'running' }

let jobs: SolveJob[] = []

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

afterEach(() => cleanup())
beforeEach(() => { jobs = [] })

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <SolveQueuePanel />
    </QueryClientProvider>,
  )
}

describe('SolveQueuePanel redaction', () => {
  it('renders a fixed label instead of an empty name for a redacted row', () => {
    jobs = [redactedJob]
    renderPanel()
    expect(screen.getByText(REDACTED_PROJECT_LABEL)).toBeTruthy()
    expect(REDACTED_PROJECT_LABEL.length).toBeGreaterThan(0)
  })

  it('disables the expand control on a redacted row', () => {
    jobs = [redactedJob]
    renderPanel()
    const expand = screen.getByTitle('Not available for this job')
    expect((expand as HTMLButtonElement).disabled).toBe(true)
  })

  it('does not claim the active editor is busy while the queue runs', () => {
    jobs = [runningJob]
    renderPanel()
    expect(screen.queryByText(/the active editor is busy/i)).toBeNull()
    expect(screen.getByText(/other projects stay editable/i)).toBeTruthy()
  })
})
