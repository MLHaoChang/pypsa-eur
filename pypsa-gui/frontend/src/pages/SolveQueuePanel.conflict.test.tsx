// A 409 on enqueue has two shapes: the older string `detail`, and the
// `solve_config_conflict` object (routers/solve_queue.py). The toast must
// render text for both, never hand react-hot-toast an object.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import SolveQueuePanel from './SolveQueuePanel'

const mutateAsync = vi.fn()

vi.mock('react-hot-toast', () => ({ default: { error: vi.fn(), success: vi.fn() } }))
vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ user: { is_super_admin: true, role: 'admin' } }) }))
vi.mock('../store/uiStore', () => ({
  useUIStore: () => ({ currentProject: null, openTabs: [{ name: 'demo', lastInteractedAt: 0 }], markProjectSaved: vi.fn() }),
}))
vi.mock('../hooks/useSolveQueue', () => ({
  useSolveQueue: () => ({ data: { jobs: [], running: [], paused: false }, isLoading: false, isError: false }),
  useEnqueueSolve: () => ({ mutate: vi.fn(), mutateAsync, isPending: false }),
  useAbortJob: () => ({ mutate: vi.fn(), isPending: false }),
  useClearFinished: () => ({ mutate: vi.fn(), isPending: false }),
  usePauseQueue: () => ({ mutate: vi.fn(), isPending: false }),
  useResumeQueue: () => ({ mutate: vi.fn(), isPending: false }),
  useCancelQueued: () => ({ mutate: vi.fn(), isPending: false }),
  useRequeueJob: () => ({ mutate: vi.fn(), isPending: false }),
  useDismissJob: () => ({ mutate: vi.fn(), isPending: false }),
}))

afterEach(() => cleanup())
beforeEach(() => vi.clearAllMocks())

async function addDemo() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SolveQueuePanel />
    </QueryClientProvider>,
  )
  fireEvent.click(screen.getByRole('button', { name: /demo/ }))
}

describe('enqueue 409 toast', () => {
  it('shows the message of a solve_config_conflict object detail', async () => {
    mutateAsync.mockRejectedValue({ response: { status: 409, data: { detail: {
      error_kind: 'solve_config_conflict', message: 'other settings are queued', job_id: 'j' } } } })
    await addDemo()
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('other settings are queued'))
  })

  it('keeps showing a string detail for the other 409s', async () => {
    mutateAsync.mockRejectedValue({ response: { status: 409, data: { detail: 'busy right now' } } })
    await addDemo()
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('busy right now'))
  })

  it('never passes an object to the toast', async () => {
    mutateAsync.mockRejectedValue({ response: { status: 409, data: { detail: { error_kind: 'capture_project_mismatch', message: 'x' } } } })
    await addDemo()
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(typeof (toast.error as any).mock.calls[0][0]).toBe('string')
  })
})
