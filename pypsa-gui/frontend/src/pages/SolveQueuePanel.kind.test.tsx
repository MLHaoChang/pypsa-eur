// A planning → dynamics study runs as a `kind: 'gridspine'` job in the ordinary
// solve queue (increment 4). The queue panel must therefore say WHAT a row is:
// a study has no objective and no network to preview, so a completed study
// row that showed the solve's "€—" line would read as a broken solve. These
// tests pin the label, the replacement text, and that a job with no `kind` at
// all (a row written before the backend grew the field) still renders as the
// plain solve it is.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { SolveJob } from '../api/solveQueue'
import SolveQueuePanel from './SolveQueuePanel'

const base: SolveJob = {
  id: '11111111-1111-4111-8111-111111111111',
  project_id: 'demo',
  project_key: 'org:demo',
  status: 'completed',
  position: null,
  objective: 1234.0,
  solve_time: 1.0,
  condition: 'optimal',
  error: null,
  enqueued_at: 0,
  started_at: 0,
  finished_at: 1,
}

let jobs: SolveJob[] = []

vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ user: null }) }))
vi.mock('../store/uiStore', () => ({
  useUIStore: () => ({ currentProject: null, openTabs: [], markProjectSaved: vi.fn() }),
}))
vi.mock('../hooks/useSolveQueue', () => ({
  useSolveQueue: () => ({ data: { jobs, current: null }, isLoading: false, isError: false }),
  useEnqueueSolve: () => ({ mutate: vi.fn(), isPending: false }),
  useAbortJob: () => ({ mutate: vi.fn(), isPending: false }),
  useClearFinished: () => ({ mutate: vi.fn(), isPending: false }),
}))

afterEach(() => cleanup())
beforeEach(() => { jobs = [] })

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><SolveQueuePanel /></QueryClientProvider>)
}

describe('SolveQueuePanel — job kind', () => {
  it('labels a gridspine job as a study and replaces the objective line', () => {
    jobs = [{ ...base, kind: 'gridspine', objective: null, solve_time: null, condition: null }]
    renderPanel()
    expect(screen.getByText('study')).toBeTruthy()
    expect(screen.getByText(/study finished/i)).toBeTruthy()
    expect(screen.queryByText(/€/)).toBeNull()
  })

  it('renders a plain solve exactly as before — no study label, objective shown', () => {
    jobs = [{ ...base, kind: 'solve' }]
    renderPanel()
    expect(screen.queryByText('study')).toBeNull()
    expect(screen.getByText(/€1\.2 k/)).toBeTruthy()
  })

  it('treats a job with no kind (a pre-field row) as a solve', () => {
    jobs = [{ ...base }]
    renderPanel()
    expect(screen.queryByText('study')).toBeNull()
    expect(screen.getByText(/€1\.2 k/)).toBeTruthy()
  })
})
