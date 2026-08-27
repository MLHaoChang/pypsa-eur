// R27 — `interrupted` is its own status with its own label and icon.
//
// The point of durability is that the user did NOT stop this job: the process
// died under it. Rendering it as "Aborted" would say the opposite, and the two
// have different remedies — an aborted job was a decision, an interrupted one
// is a candidate for requeue.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { isTerminal, TERMINAL_STATUSES, type SolveJob } from '../api/solveQueue'
import SolveQueuePanel, { canExpandJob } from './SolveQueuePanel'

const interruptedJob: SolveJob = {
  id: '11111111-1111-4111-8111-111111111111',
  project_id: 'crashed', project_key: null, status: 'interrupted',
  position: null, objective: null, solve_time: null,
  condition: 'process_exited', error: null,
  enqueued_at: 0, started_at: 0, finished_at: 1,
  // Not the subject of this file; false keeps the Dismiss control out of
  // the DOM so it cannot interfere with the queries below.
  can_dismiss: false,
}

let jobs: SolveJob[] = []

vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ user: null }) }))
vi.mock('../store/uiStore', () => ({
  useUIStore: () => ({ currentProject: null, openTabs: [], markProjectSaved: vi.fn() }),
}))
vi.mock('../hooks/useSolveQueue', () => ({
  useSolveQueue: () => ({ data: { jobs, running: [], paused: false }, isLoading: false, isError: false }),
  useEnqueueSolve: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useAbortJob: () => ({ mutate: vi.fn(), isPending: false }),
  useClearFinished: () => ({ mutate: vi.fn(), isPending: false }),
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
beforeEach(() => { jobs = [interruptedJob] })

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <SolveQueuePanel />
    </QueryClientProvider>,
  )
}

describe('interrupted', () => {
  it('is a terminal status', () => {
    expect(TERMINAL_STATUSES.has('interrupted')).toBe(true)
    expect(isTerminal(interruptedJob)).toBe(true)
  })

  it('is expandable like any other terminal job', () => {
    expect(canExpandJob(interruptedJob)).toBe(true)
  })

  it('renders its own label, not "Aborted"', () => {
    renderPanel()
    expect(screen.getByText('Interrupted')).toBeTruthy()
    expect(screen.queryByText('Aborted')).toBeNull()
  })

  it('says the process stopped it, not the user', () => {
    renderPanel()
    expect(screen.getByText(/did not finish|stopped by a restart/i)).toBeTruthy()
    expect(screen.queryByText(/aborted by user/i)).toBeNull()
  })
})
