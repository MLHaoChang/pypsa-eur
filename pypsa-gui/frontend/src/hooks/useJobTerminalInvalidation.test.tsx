// Fix round 1 (Task 7 review): `useJobTerminalInvalidation.test.ts` proves the
// pure `statusMap`/`terminalTransitions` diffing logic is correct in isolation,
// but nothing exercised the hook's actual React Query wiring — the code at
// `useJobTerminalInvalidation.ts:55-71` that calls `qc.invalidateQueries(...)`,
// which is the only code in the diff that fulfills R9. `ProjectTabs.test.tsx`
// mounts the hook via `<ProjectTabs/>`, but its `useSolveQueue` mock is pinned
// to `{ jobs: [] }` for every one of its 9 tests, so the `useEffect` body only
// ever runs its no-op branch (`finished` is always `[]`). A wiring bug — an
// inverted dependency array, a typo'd key root, or `prevRef.current` reset in
// the wrong order — would pass every test that existed before this file.
//
// These three tests render the hook directly under a QueryClientProvider with
// a CONTROLLED `useSolveQueue` mock (driven across renders, mirroring the real
// 1.5s poll where `data.jobs` is a fresh array reference each time) and spy on
// the real `QueryClient.invalidateQueries`, asserting the actual key arrays it
// is called with — not just that the pure functions return the right array.
//
// .tsx (not .test.ts): the wrapper below needs JSX, and this project's vite
// config maps `.ts` to esbuild's `ts` loader (no JSX) — only `.tsx` gets the
// `tsx` loader. See `useResultsWindow.test.tsx`'s header comment for the same
// rule applied there.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useJobTerminalInvalidation } from './useJobTerminalInvalidation'
import { useSolveQueue } from './useSolveQueue'
import { nk } from '../utils/queryKeys'
import type { SolveJob } from '../api/solveQueue'

vi.mock('./useSolveQueue', () => ({ useSolveQueue: vi.fn() }))

// Same `job` helper as useJobTerminalInvalidation.test.ts — `id` is a UUID
// string here for the same reason (see that file's header comment).
function job(id: string, project_id: string | null, status: SolveJob['status']): SolveJob {
  return {
    id, project_id, project_key: null, status,
    position: null, objective: null, solve_time: null, condition: null, error: null,
    enqueued_at: 0, started_at: null, finished_at: null,
  }
}

// Drives the mocked useSolveQueue()'s return for the NEXT render — a fresh
// array literal each call, matching the real hook's `data.jobs` identity
// changing on every poll response (see the deferred-minor note in the task 7
// report: the effect re-running per-poll is intentional, not the bug here).
function setJobs(jobs: SolveJob[]) {
  vi.mocked(useSolveQueue).mockReturnValue({ data: { jobs, current: null } } as never)
}

function wrapper(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
}

function makeClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

beforeEach(() => { vi.clearAllMocks() })

describe('useJobTerminalInvalidation wiring', () => {
  it("invalidates all three keys for the finishing job's project only, on the render where it transitions", () => {
    const client = makeClient()
    const spy = vi.spyOn(client, 'invalidateQueries')
    setJobs([job('1', 'alpha', 'running'), job('2', 'beta', 'queued')])
    const { rerender } = renderHook(() => useJobTerminalInvalidation(), { wrapper: wrapper(client) })
    // First render only SEEDS prevRef — no prior snapshot to diff against, so
    // nothing should invalidate yet (mirrors the pure-function "first poll"
    // case, but exercised through the actual mount + effect this time).
    expect(spy).not.toHaveBeenCalled()

    setJobs([job('1', 'alpha', 'completed'), job('2', 'beta', 'queued')])
    rerender()

    expect(spy).toHaveBeenCalledWith({ queryKey: nk('alpha', 'results') })
    expect(spy).toHaveBeenCalledWith({ queryKey: nk('alpha', 'simulationStatus') })
    expect(spy).toHaveBeenCalledWith({ queryKey: nk('alpha', 'meta') })
    expect(spy).toHaveBeenCalledTimes(3)
    // 'beta' stayed queued the whole time — must not be touched.
    expect(spy).not.toHaveBeenCalledWith({ queryKey: nk('beta', 'results') })
  })

  it('does not invalidate again on a later render where the job is STILL terminal (prevRef threading)', () => {
    // The property the pure-function tests cannot see: they pass `prev`
    // explicitly per call, so they can't catch prevRef failing to persist (or
    // being reset) across renders of the actual hook.
    const client = makeClient()
    const spy = vi.spyOn(client, 'invalidateQueries')
    setJobs([job('1', 'alpha', 'running')])
    const { rerender } = renderHook(() => useJobTerminalInvalidation(), { wrapper: wrapper(client) })

    setJobs([job('1', 'alpha', 'completed')])
    rerender()
    expect(spy).toHaveBeenCalledTimes(3)

    spy.mockClear()
    // A later poll response with a fresh array reference but the SAME status
    // — the common steady-state case once a job has finished and the queue
    // keeps being polled/refetched.
    setJobs([job('1', 'alpha', 'completed')])
    rerender()
    expect(spy).not.toHaveBeenCalled()
  })

  it('invalidates nothing when a redacted row (project_id: null) transitions to terminal', () => {
    const client = makeClient()
    const spy = vi.spyOn(client, 'invalidateQueries')
    setJobs([job('1', null, 'running')])
    const { rerender } = renderHook(() => useJobTerminalInvalidation(), { wrapper: wrapper(client) })

    setJobs([job('1', null, 'completed')])
    rerender()

    expect(spy).not.toHaveBeenCalled()
  })
})
