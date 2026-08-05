// Regression test for the snapshot-loading race flagged in Task 3 review
// round 1: `resolveRange`'s empty-index fallback returns {from:0, to:0} when
// `snap` hasn't loaded yet. {0,0} is a valid (non-inverted) range, so a bare
// `win.from <= win.to` gate reads the "we don't know the horizon yet" state
// as "fetch snapshot 0 only" — every windowed query in a tab fires once for
// a single row, caches it, then immediately refires with the real bounds
// once `snap` resolves. `winValid` must also require `snap` to be resolved.
//
// .tsx (not .test.ts): the wrapper below needs JSX, and this project's vite
// config maps `.ts` to esbuild's `ts` loader (no JSX) — only `.tsx` gets the
// `tsx` loader. `src/utils/rescaleActions.test.ts` is the counter-example:
// pure-helper tests with no JSX correctly stay `.test.ts`.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useResultsWindow } from './useResultsWindow'
import { networkApi } from '../api/network'
import { ResultsFilterProvider, type ResultsFilter } from '../pages/results/filterContext'
import type { SnapshotInfo } from '../api/types'

vi.mock('../api/network', () => ({
  networkApi: { getSnapshots: vi.fn() },
}))

function makeClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

function wrapperWithFilter(client: QueryClient, filter?: ResultsFilter) {
  return function Wrapper({ children }: { children: ReactNode }) {
    const body = filter
      ? <ResultsFilterProvider value={filter}>{children}</ResultsFilterProvider>
      : children
    return <QueryClientProvider client={client}>{body}</QueryClientProvider>
  }
}

const getSnapshots = vi.mocked(networkApi.getSnapshots)

beforeEach(() => { getSnapshots.mockReset() })
afterEach(() => { vi.clearAllMocks() })

describe('useResultsWindow', () => {
  it('winValid is false while the snapshot query is pending', () => {
    // Never resolves within the test — captures the moment right after
    // mount, before react-query's fetch promise has had a chance to settle.
    getSnapshots.mockReturnValue(new Promise<SnapshotInfo>(() => {}))
    const client = makeClient()
    const { result } = renderHook(() => useResultsWindow('proj'), {
      wrapper: wrapperWithFilter(client),
    })
    // Pre-fix, resolveRange's empty-index fallback ({from:0,to:0}) made this
    // `true` here — a phantom "snapshot 0 only" window.
    expect(result.current.winValid).toBe(false)
  })

  it('winValid becomes true with the real bounds once snapshots resolve', async () => {
    const snapshots = [
      '2026-01-01T00:00:00', '2026-01-01T01:00:00', '2026-01-01T02:00:00',
    ]
    getSnapshots.mockResolvedValue({ count: 3, snapshots, weightings: [] })
    const client = makeClient()
    const { result } = renderHook(() => useResultsWindow('proj'), {
      wrapper: wrapperWithFilter(client),
    })
    expect(result.current.winValid).toBe(false)
    await waitFor(() => expect(result.current.winValid).toBe(true))
    expect(result.current.win).toEqual({ from: 0, to: 2 })
    // The snapshots query itself fires exactly once — no phantom "snapshot 0"
    // round-trip before the real one resolves. This does NOT cover the seven
    // downstream Dispatch-style queries keyed on `win.from`/`win.to`; those
    // are asserted per-tab (e.g. Dispatch.test.tsx), not here.
    expect(getSnapshots).toHaveBeenCalledTimes(1)
  })

  it('a selected period absent from the network still resolves to winValid=false (must survive)', async () => {
    const snapshots = ['2026-01-01T00:00:00', '2027-01-01T00:00:00']
    getSnapshots.mockResolvedValue({
      count: 2, snapshots, weightings: [], periods: [2026, 2027],
    })
    const client = makeClient()
    const filter: ResultsFilter = { fromIso: null, toIso: null, selectedPeriod: 2099 }
    const { result } = renderHook(() => useResultsWindow('proj'), {
      wrapper: wrapperWithFilter(client, filter),
    })
    // Collapsed range once resolveRange has real data to work with —
    // resolveRange's "period not present" branch: {from: index.length, to: -1}.
    await waitFor(() => expect(result.current.win).toEqual({ from: 2, to: -1 }))
    expect(result.current.winValid).toBe(false)
  })

  // FIX 1 (results-tabs-window final review, Critical): the six windowing
  // tabs pass `fetchRange` — not `win` — to their `resultsApi.get*` calls.
  // `win` still drives the query key so caching-by-window-position keeps
  // working; `fetchRange` is what actually goes out on the wire, and it must
  // be `undefined` whenever `win` covers the whole horizon, so the request
  // has no `from`/`to` and can never hit the backend's MAX_RESPONSE_VALUES
  // cap (`_wants_slice` in routers/results.py only slices — and therefore
  // only caps — when bounds are present). These two tests are the
  // discrimination proof: reverting `fetchRange` to always return `win`
  // (i.e. dropping the `isWhole` branch) makes the first test fail, because
  // `fetchRange` would then be `{from: 0, to: 2}` instead of `undefined`.
  it('fetchRange is undefined when the filter covers the whole horizon', async () => {
    const snapshots = [
      '2026-01-01T00:00:00', '2026-01-01T01:00:00', '2026-01-01T02:00:00',
    ]
    getSnapshots.mockResolvedValue({ count: 3, snapshots, weightings: [] })
    const client = makeClient()
    const { result } = renderHook(() => useResultsWindow('proj'), {
      wrapper: wrapperWithFilter(client),
    })
    await waitFor(() => expect(result.current.winValid).toBe(true))
    // Observed: win = {from: 0, to: 2} (the full 3-snapshot horizon) —
    // total - 1 === 2, so isWhole is true and fetchRange drops to undefined.
    expect(result.current.win).toEqual({ from: 0, to: 2 })
    expect(result.current.fetchRange).toBeUndefined()
  })

  it('fetchRange equals win when the filter narrows the window', async () => {
    const snapshots = [
      '2026-01-01T00:00:00', '2026-01-01T01:00:00', '2026-01-01T02:00:00',
    ]
    getSnapshots.mockResolvedValue({ count: 3, snapshots, weightings: [] })
    const client = makeClient()
    // Narrow to the first two snapshots only — win no longer covers the
    // whole horizon (total - 1 === 2, win.to === 1).
    const filter: ResultsFilter = {
      fromIso: snapshots[0], toIso: snapshots[1], selectedPeriod: null,
    }
    const { result } = renderHook(() => useResultsWindow('proj'), {
      wrapper: wrapperWithFilter(client, filter),
    })
    await waitFor(() => expect(result.current.winValid).toBe(true))
    // Observed: win = {from: 0, to: 1} — narrower than the full {0, 2}
    // horizon, so fetchRange must equal win (the ranged path is correct
    // here; the server SHOULD be asked to slice).
    expect(result.current.win).toEqual({ from: 0, to: 1 })
    expect(result.current.fetchRange).toEqual(result.current.win)
  })
})
