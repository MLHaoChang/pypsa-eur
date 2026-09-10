// R20 — the expand control shows the LIVE log for a running row and the
// RETAINED log for a terminal row, for all four terminal statuses.
//
// Before this, expand was enabled only for `completed` and only ever showed the
// results bundle: a failed job's output was unreachable from the panel, and a
// running job's log could only be read by being on the project that owned it.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { SolveJob, SolveJobStatus } from '../api/solveQueue'
import SolveQueuePanel, { canExpandJob } from './SolveQueuePanel'

const JOB_ID = '55555555-5555-4555-8555-555555555555'

function job(status: SolveJobStatus, project_id: string | null = 'demo'): SolveJob {
  return {
    id: JOB_ID, project_id, project_key: null, status,
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
    jobLogHistory: (id: string) => jobLogHistory(id),
    jobLogStreamUrl: (id: string) => `/api/simulation/queue/${id}/log_stream`,
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
    expect(jobLogHistory).toHaveBeenCalledWith(JOB_ID)
  })
})

// ── Live stream (running row) ───────────────────────────────────────────────
// jsdom has no EventSource. This repo's own idiom for that gap (vitest.setup.ts
// — ResizeObserver, window.matchMedia, Element.scrollIntoView) is to stub the
// missing browser API rather than leave the dependent code path untested, so a
// FakeEventSource is used here the same way.
class FakeEventSource {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSED = 2
  static instances: FakeEventSource[] = []
  url: string
  readyState: number = FakeEventSource.OPEN
  onmessage: ((e: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  private listeners: Record<string, Array<(e: { data: string }) => void>> = {}

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }
  addEventListener(type: string, cb: (e: { data: string }) => void) {
    (this.listeners[type] ??= []).push(cb)
  }
  close() { this.readyState = FakeEventSource.CLOSED }
  emitMessage(data: string) { this.onmessage?.({ data }) }
  emitDone(data: unknown) {
    const ev = { data: JSON.stringify(data) }
    this.listeners.done?.forEach(cb => cb(ev))
  }
  emitError() { this.onerror?.() }
}

describe('SolveQueuePanel live log stream', () => {
  beforeEach(() => {
    FakeEventSource.instances = []
    vi.stubGlobal('EventSource', FakeEventSource)
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('opens the job’s own stream for a running row, appends live lines, and closes on done', async () => {
    jobs = [job('running')]
    renderPanel()
    await userEvent.click(screen.getByTitle('Show this job’s log'))

    const es = FakeEventSource.instances[0]
    expect(es?.url).toBe(`/api/simulation/queue/${JOB_ID}/log_stream`)

    act(() => { es.emitMessage('solver: iteration 1') })
    act(() => { es.emitMessage('solver: iteration 2') })
    expect(screen.getByText(/solver: iteration 1/)).toBeTruthy()
    expect(screen.getByText(/solver: iteration 2/)).toBeTruthy()
    // The stream is the ONLY history source while live (Deviation 2 in the
    // task report) — a running row must never also hit the REST endpoint.
    expect(jobLogHistory).not.toHaveBeenCalled()

    act(() => { es.emitDone({ status: 'completed' }) })
    expect(es.readyState).toBe(FakeEventSource.CLOSED)
  })

  it('tolerates a transient error while the job is confirmed still running', async () => {
    // Review Important 1 — a naive `onerror = () => es.close()` froze the log
    // permanently on the first blip. This pins the fix's tolerant branch: a
    // stale gap that the job's own status says is still `running` must NOT
    // surface an error or close the connection.
    jobs = [job('running')]
    jobLogHistory.mockResolvedValue({ lines: [], status: 'running' })
    const now = vi.spyOn(Date, 'now').mockReturnValue(1_000)
    renderPanel()
    await userEvent.click(screen.getByTitle('Show this job’s log'))
    const es = FakeEventSource.instances[0]

    act(() => { es.emitMessage('solver: iteration 1') })
    now.mockReturnValue(1_000 + 31_000) // past STALE_MS since the last event
    act(() => { es.emitError() })

    await waitFor(() => expect(jobLogHistory).toHaveBeenCalledWith(JOB_ID))
    expect(screen.queryByText(/Log stream lost/)).toBeNull()
    expect(es.readyState).not.toBe(FakeEventSource.CLOSED)
  })

  it('gives up once the job is confirmed no longer running, closing the stream', async () => {
    // The other half of the same branch: once the authoritative check says
    // the job has actually finished, the panel must stop silently freezing
    // and instead say so — `live` flipping false on the next queue poll then
    // re-fetches the authoritative retained log via the other branch.
    jobs = [job('running')]
    jobLogHistory.mockResolvedValue({ lines: [], status: 'completed' })
    const now = vi.spyOn(Date, 'now').mockReturnValue(1_000)
    renderPanel()
    await userEvent.click(screen.getByTitle('Show this job’s log'))
    const es = FakeEventSource.instances[0]

    act(() => { es.emitMessage('solver: iteration 1') })
    now.mockReturnValue(1_000 + 31_000)
    act(() => { es.emitError() })

    await waitFor(() => expect(screen.getByText('Log stream lost — the job has since finished.')).toBeTruthy())
    expect(es.readyState).toBe(FakeEventSource.CLOSED)
  })

  it('reports an immediate loss once the browser’s own reconnect budget is exhausted', async () => {
    // readyState CLOSED means the browser has already given up retrying —
    // unlike a transient error, nothing further can arrive, so this must NOT
    // wait for STALE_MS or consult jobLogHistory before saying so.
    jobs = [job('running')]
    renderPanel()
    await userEvent.click(screen.getByTitle('Show this job’s log'))
    const es = FakeEventSource.instances[0]
    es.readyState = FakeEventSource.CLOSED

    act(() => { es.emitError() })
    expect(screen.getByText('Log stream lost before the job finished.')).toBeTruthy()
    expect(jobLogHistory).not.toHaveBeenCalled()
  })

  it('closes the stream when the verification check itself is unreachable, without a request storm', async () => {
    // Review round 2 Important — the original `.catch` set an error but never
    // closed `es`. A real browser keeps auto-reconnecting a connection that
    // isn't closed, re-firing `onerror` roughly every ~3s; since no message
    // ever arrives to advance `lastEventAt`, every retry re-entered this same
    // stale branch and fired ANOTHER jobLogHistory request — unbounded, for
    // as long as the row stayed expanded, against a backend that had just
    // reported itself unreachable. `toHaveBeenCalledTimes(1)` pins that this
    // can no longer happen: once closed, no further verification call fires.
    jobs = [job('running')]
    jobLogHistory.mockRejectedValue(new Error('network down'))
    const now = vi.spyOn(Date, 'now').mockReturnValue(1_000)
    renderPanel()
    await userEvent.click(screen.getByTitle('Show this job’s log'))
    const es = FakeEventSource.instances[0]

    act(() => { es.emitMessage('solver: iteration 1') })
    now.mockReturnValue(1_000 + 31_000)
    act(() => { es.emitError() })

    await waitFor(() => expect(screen.getByText('Log stream silent and unreachable — connection lost.')).toBeTruthy())
    expect(es.readyState).toBe(FakeEventSource.CLOSED)
    expect(jobLogHistory).toHaveBeenCalledTimes(1)
  })

  it('clears a stale error banner once the stream recovers on its own', async () => {
    // Pins the `setError(null)` added to `onmessage` — without it, a
    // connection that heals on its own (browser's own auto-reconnect
    // succeeds) leaves a stale "connection lost" banner permanently masking
    // live data that is actually accumulating fine behind it, since the
    // render branch shows the error INSTEAD of the lines whenever `error` is
    // set.
    jobs = [job('running')]
    renderPanel()
    await userEvent.click(screen.getByTitle('Show this job’s log'))
    const es = FakeEventSource.instances[0]

    act(() => { es.emitMessage('solver: iteration 1') })
    es.readyState = FakeEventSource.CLOSED
    act(() => { es.emitError() })
    expect(screen.getByText('Log stream lost before the job finished.')).toBeTruthy()

    // The browser reconnected on its own and a line arrives again.
    es.readyState = FakeEventSource.OPEN
    act(() => { es.emitMessage('solver: iteration 2') })
    expect(screen.queryByText(/Log stream lost/)).toBeNull()
    expect(screen.getByText(/solver: iteration 2/)).toBeTruthy()
  })

  it('does not let a delayed stale-check resolution overwrite a `done` that arrived in the meantime', async () => {
    // Minor from round 2 — a `done` arriving while the stale-branch
    // `jobLogHistory` call is still in flight must win. Without the
    // `doneReceived` guard added to the `.then()`, the delayed resolution
    // could overwrite a cleanly-finished view with a spurious
    // "Log stream lost" error.
    jobs = [job('running')]
    let resolveHistory: (v: { lines: string[]; status: SolveJobStatus }) => void = () => {}
    jobLogHistory.mockImplementation(() => new Promise(resolve => { resolveHistory = resolve }))
    const now = vi.spyOn(Date, 'now').mockReturnValue(1_000)
    renderPanel()
    await userEvent.click(screen.getByTitle('Show this job’s log'))
    const es = FakeEventSource.instances[0]

    act(() => { es.emitMessage('solver: iteration 1') })
    now.mockReturnValue(1_000 + 31_000)
    act(() => { es.emitError() }) // stale check fires; jobLogHistory left pending
    await waitFor(() => expect(jobLogHistory).toHaveBeenCalledWith(JOB_ID))

    // `done` arrives before the stale check's REST call resolves.
    act(() => { es.emitDone({ status: 'completed' }) })
    expect(es.readyState).toBe(FakeEventSource.CLOSED)

    // The delayed history resolution must not resurrect an error over the
    // now-cleanly-finished view.
    await act(async () => {
      resolveHistory({ lines: [], status: 'completed' })
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(screen.queryByText(/Log stream lost/)).toBeNull()
  })
})
