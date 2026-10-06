// The A2 detection hook's meta poll and sample rule, with the real timings
// (P27b gate notes 1 and 2). Fake timers; `/network/meta` is a spy.
//   • poll fast (confirmMs) ONLY for the one re-check that confirms a first
//     disagreeing sample — a study tab (never raisable) polled at 1 Hz forever;
//   • a sample is a completed fetch: an effect re-run without a new sample (a
//     failed refetch between two samples) must not count as the second one.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const getMeta = vi.fn()
vi.mock('../api/network', () => ({ networkApi: { getMeta: (...a: unknown[]) => getMeta(...a) } }))
const { useUIStore } = await import('../store/uiStore')
const { useProjectMismatchDetection, mismatchPoll } = await import('./useProjectMismatchDetection')

function H({ study }: { study: boolean }) { useProjectMismatchDetection(study); return null }
const meta = (loaded_project: string | null) => ({ name: 'n', bus_count: 3, snapshot_count: 1, loaded_project })

let qc: QueryClient
async function mount(study: boolean, tab: string, backend: string | null) {
  vi.useFakeTimers()
  getMeta.mockReset().mockResolvedValue(meta(backend))
  useUIStore.setState({ currentProject: tab, projectMismatch: null, projectSwitchInProgress: false })
  qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><H study={study} /></QueryClientProvider>)
}
const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })

afterEach(() => {
  qc?.clear()
  vi.useRealTimers()
  useUIStore.setState({ projectMismatch: null, projectSwitchInProgress: false })
})

describe('meta poll rate (defaults: idle 5 s, one 1 s confirm)', () => {
  it('uses the documented defaults', () => {
    expect(mismatchPoll).toEqual({ idleMs: 5_000, confirmMs: 1_000 })
  })

  it('an agreeing tab: 13 reads in 60 s', async () => {
    await mount(false, 'X', 'X')
    await advance(60_000)
    expect(getMeta).toHaveBeenCalledTimes(13)
  })

  // t = 0 (first sample), 1 (confirm → raised), 2 (the interval for the t=1
  // sample was computed before the effect raised it), then idle 7, …, 57 → 14.
  it('a mismatched tab: raised on the 1 s re-check, then the idle poll (14 reads in 60 s)', async () => {
    await mount(false, 'X', 'Y')
    await advance(1_100)
    expect(useUIStore.getState().projectMismatch).toEqual({ tab: 'X', backend: 'Y' })
    await advance(58_900)
    expect(getMeta).toHaveBeenCalledTimes(14)
  })

  // 59 s at the idle rate: t = 0, 5, …, 55 → 12 reads (one 1 s re-check
  // would make it 13, a 1 Hz poll 60).
  it('a study tab (never raisable) polls at the idle rate, not 1 Hz (12 reads in 59 s)', async () => {
    await mount(true, 'Study1', 'X')
    await advance(59_000)
    expect(getMeta).toHaveBeenCalledTimes(12)
    expect(useUIStore.getState().projectMismatch).toBeNull()
  })

  it('a switch that stays in flight does not use the fast poll either (12 reads in 59 s)', async () => {
    useUIStore.setState({ projectSwitchInProgress: true })
    vi.useFakeTimers()
    getMeta.mockReset().mockResolvedValue(meta('Y'))
    useUIStore.setState({ currentProject: 'X', projectMismatch: null })
    qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={qc}><H study={false} /></QueryClientProvider>)
    await advance(59_000)
    expect(getMeta).toHaveBeenCalledTimes(12)
    expect(useUIStore.getState().projectMismatch).toBeNull()
  })
})

describe('what counts as a sample', () => {
  it('a failed refetch between two samples is not the second disagreeing sample', async () => {
    mismatchPoll.confirmMs = 2 ** 30
    mismatchPoll.idleMs = 2 ** 30
    try {
      await mount(false, 'X', 'Y')
      await advance(10)
      expect(getMeta).toHaveBeenCalledTimes(1)
      // the backend is down for one read: the query errors, keeps its data,
      // and the effect re-runs on the same sample
      let fail: (e: Error) => void = () => {}
      getMeta.mockImplementationOnce(() => new Promise((_, rej) => { fail = rej }))
      act(() => { void qc.refetchQueries({ queryKey: ['meta', 'X'] }).catch(() => {}) })
      await advance(10)          // rendered with isFetching: true
      act(() => { fail(new Error('Network Error')) })
      await advance(10)          // rendered settled again — same sample, same data
      expect(getMeta).toHaveBeenCalledTimes(2)
      expect(useUIStore.getState().projectMismatch).toBeNull()
      // the next real sample confirms it
      await act(async () => { await qc.refetchQueries({ queryKey: ['meta', 'X'] }) })
      await advance(10)
      expect(useUIStore.getState().projectMismatch).toEqual({ tab: 'X', backend: 'Y' })
    } finally {
      mismatchPoll.confirmMs = 1_000
      mismatchPoll.idleMs = 5_000
    }
  })
})
