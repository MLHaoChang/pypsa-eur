// Phase 2 plan Tasks 4.2 / 5.1: the 3D view owns a status poll, because the
// status bar polls only after an in-session solve (store 'completed' or
// 'failed'); a loaded, solved project leaves the store 'idle'.
import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { simulationApi } from '../api/simulation'
import { nk } from '../utils/queryKeys'
import { useDispatchFresh, useRefetchOnSolve, STATUS_POLL_MS } from './useDispatchFresh'

vi.mock('../api/simulation')

let qc: QueryClient
const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>

beforeEach(() => {
  qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  vi.mocked(simulationApi.getStatus).mockReset()
})
afterEach(() => { vi.useRealTimers() })

describe('useDispatchFresh', () => {
  it.each([
    ['fresh', true], ['stale', false], ['none', false], [undefined, false],
  ] as const)('dispatch %s → fresh %s', async (dispatch, fresh) => {
    vi.mocked(simulationApi.getStatus).mockResolvedValue({ status: 'idle', dispatch } as never)
    const { result } = renderHook(() => useDispatchFresh('p'), { wrapper })
    await waitFor(() => expect(simulationApi.getStatus).toHaveBeenCalled())
    await waitFor(() => expect(result.current.dispatch).toBe(dispatch))
    expect(result.current.fresh).toBe(fresh)
  })
  it('polls every few seconds while mounted, on the status bar\'s query key', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.mocked(simulationApi.getStatus).mockResolvedValue({ status: 'idle', dispatch: 'fresh' } as never)
    const { unmount } = renderHook(() => useDispatchFresh('p'), { wrapper })
    await waitFor(() => expect(simulationApi.getStatus).toHaveBeenCalledTimes(1))
    await act(async () => { await vi.advanceTimersByTimeAsync(STATUS_POLL_MS + 50) })
    await waitFor(() => expect(simulationApi.getStatus).toHaveBeenCalledTimes(2))
    expect(qc.getQueryData(nk('p', 'simulationStatus'))).toEqual({ status: 'idle', dispatch: 'fresh' })
    unmount()
    await act(async () => { await vi.advanceTimersByTimeAsync(3 * STATUS_POLL_MS) })
    expect(simulationApi.getStatus).toHaveBeenCalledTimes(2)
  })
  it('does not poll without a project', async () => {
    renderHook(() => useDispatchFresh(null), { wrapper })
    await new Promise(r => setTimeout(r, 20))
    expect(simulationApi.getStatus).not.toHaveBeenCalled()
  })
})

describe('useRefetchOnSolve', () => {
  // A solve writes *_nom_opt into the component lists, and no solve path
  // invalidates them (only results and status): optimised sizes would be
  // the previous solve's.
  const st = (dispatch: string, objective: number, solve_time = 1) => ({ status: 'completed', dispatch, objective, solve_time })
  it('invalidates the given lists when a new solve turns fresh, not on mount or while stale', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const spy = vi.spyOn(qc, 'invalidateQueries')
    vi.mocked(simulationApi.getStatus).mockResolvedValue(st('fresh', 10) as never)
    renderHook(() => { const f = useDispatchFresh('p'); useRefetchOnSolve('p', f.solveId, ['generators', 'storage_units']) }, { wrapper })
    await waitFor(() => expect(simulationApi.getStatus).toHaveBeenCalledTimes(1))
    await act(async () => { await vi.advanceTimersByTimeAsync(10) })
    const lists = () => spy.mock.calls.map(c => JSON.stringify((c[0] as { queryKey: unknown }).queryKey))
    expect(lists()).toEqual([]) // first sight: the lists were just fetched
    vi.mocked(simulationApi.getStatus).mockResolvedValue(st('none', 10) as never) // an edit
    await act(async () => { await vi.advanceTimersByTimeAsync(STATUS_POLL_MS + 50) })
    expect(lists()).toEqual([])
    vi.mocked(simulationApi.getStatus).mockResolvedValue(st('fresh', 12, 2) as never) // re-solved
    await act(async () => { await vi.advanceTimersByTimeAsync(STATUS_POLL_MS + 50) })
    await waitFor(() => expect(lists()).toEqual([JSON.stringify(nk('p', 'generators')), JSON.stringify(nk('p', 'storage_units'))]))
    spy.mockClear()
    await act(async () => { await vi.advanceTimersByTimeAsync(STATUS_POLL_MS + 50) }) // same solve, polled again
    expect(lists()).toEqual([])
  })
})
