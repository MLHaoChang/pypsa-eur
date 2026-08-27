// P-1: `POST /api/simulation/queue/clear_finished` is gated server-side on
// `is_super_admin` (routers/solve_queue.py) because the solve queue is
// process-global and shared across organisations, so a global clear crosses
// org boundaries where an org admin has no authority.
//
// The control must match the route exactly. The trap is `useAuth().isAdmin`,
// which is `hasAdminConsoleAccess` (pages/admin/helpers.ts:9-11) and is ALSO
// true for an ORG admin (`role === 'admin'`) — gating on it would leave an org
// admin with an enabled button and a guaranteed 403. These tests pin the raw
// `is_super_admin` flag instead.
//
// Note the button is DISABLED with an explanatory title rather than hidden,
// matching the sibling "Queue current project" button, which already uses
// disabled + title for its own unavailable state.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { SolveJob } from '../api/solveQueue'
import { localAdminUser } from '../auth/localMode'
import SolveQueuePanel from './SolveQueuePanel'

// One terminal job, so `finishedCount === 0` can never be the reason the button
// is disabled — otherwise these tests would pass for the wrong reason.
const finishedJob: SolveJob = {
  id: '11111111-1111-4111-8111-111111111111',
  project_id: 'demo',
  project_key: 'org:demo',
  status: 'completed',
  position: null,
  objective: 1.0,
  solve_time: 1.0,
  condition: 'optimal',
  error: null,
  enqueued_at: 0,
  started_at: 0,
  finished_at: 1,
}

let authState: { user: { is_super_admin: boolean; role: string | null } | null }

vi.mock('../auth/AuthProvider', () => ({ useAuth: () => authState }))

vi.mock('../store/uiStore', () => ({
  useUIStore: () => ({ currentProject: null, openTabs: [], markProjectSaved: vi.fn() }),
}))

vi.mock('../hooks/useSolveQueue', () => ({
  useSolveQueue: () => ({ data: { jobs: [finishedJob], running: [], paused: false }, isLoading: false, isError: false }),
  useEnqueueSolve: () => ({ mutate: vi.fn(), isPending: false }),
  useAbortJob: () => ({ mutate: vi.fn(), isPending: false }),
  useClearFinished: () => ({ mutate: vi.fn(), isPending: false }),
}))

afterEach(() => cleanup())
beforeEach(() => { authState = { user: null } })

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <SolveQueuePanel />
    </QueryClientProvider>,
  )
}

function clearButton(): HTMLButtonElement {
  return screen.getByRole('button', { name: /clear finished/i }) as HTMLButtonElement
}

describe('Clear finished — super-admin gate', () => {
  it('is disabled, with a reason, for a plain member', () => {
    authState = { user: { is_super_admin: false, role: 'member' } }
    renderPanel()
    const btn = clearButton()
    expect(btn.disabled).toBe(true)
    expect(btn.title).toMatch(/only super-admins/i)
  })

  it('is disabled for an ORG admin — isAdmin would have wrongly enabled it', () => {
    // hasAdminConsoleAccess({role:'admin'}) === true, so a fix gated on
    // useAuth().isAdmin would enable the button here and then 403.
    authState = { user: { is_super_admin: false, role: 'admin' } }
    renderPanel()
    expect(clearButton().disabled).toBe(true)
  })

  it('is enabled for a super-admin', () => {
    authState = { user: { is_super_admin: true, role: 'admin' } }
    renderPanel()
    const btn = clearButton()
    expect(btn.disabled).toBe(false)
    expect(btn.title).toMatch(/remove completed/i)
  })

  it('stays enabled in local mode — the packaged desktop app must not regress', () => {
    // local_mode.py:132 seeds is_super_admin=True and localMode.ts mirrors it.
    const local = localAdminUser()
    expect(local.is_super_admin).toBe(true)
    authState = { user: { is_super_admin: local.is_super_admin, role: local.role } }
    renderPanel()
    expect(clearButton().disabled).toBe(false)
  })
})
