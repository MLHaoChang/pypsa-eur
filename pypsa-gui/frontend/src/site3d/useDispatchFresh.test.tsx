// Phase 2 plan Tasks 4.2 / 5.1: the 3D view owns a status poll, because the
// status bar polls only after an in-session solve (store 'completed' or
// 'failed'); a loaded, solved project leaves the store 'idle'.
import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { simulationApi } from '../api/simulation'
import { nk } from '../utils/queryKeys'
import { useDispatchFresh, useSolveSettled, STATUS_POLL_MS } from './useDispatchFresh'

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

describe('useDispatchFresh render isolation', () => {
  it('a poll that brings the same status does not re-render its host', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.mocked(simulationApi.getStatus).mockImplementation(async () => ({ status: 'idle', dispatch: 'fresh', objective: 1, solve_time: 1 }) as never)
    let renders = 0
    renderHook(() => { renders++; return useDispatchFresh('p') }, { wrapper })
    await waitFor(() => expect(simulationApi.getStatus).toHaveBeenCalledTimes(1))
    await act(async () => { await vi.advanceTimersByTimeAsync(10) })
    const settled = renders
    for (let k = 0; k < 5; k++) await act(async () => { await vi.advanceTimersByTimeAsync(STATUS_POLL_MS + 10) })
    expect(simulationApi.getStatus).toHaveBeenCalledTimes(6)
    expect(renders).toBe(settled)
  })
})

describe('useSolveSettled', () => {
  // A solve writes *_nom_opt into the component lists, and no solve path
  // refetches them; an edit refetches them but never the status. So the
  // view is "current" only once the lists were refetched for this solve
  // and while they still hold that data.
  const st = (dispatch: string, objective: number, solve_time = 1) => ({ status: 'completed', dispatch, objective, solve_time })
  let gens: { name: string; p_nom_opt: number }[]
  const getGens = vi.fn(async () => gens)

  function host(project = 'p') {
    const f = useDispatchFresh(project)
    const q = useQuery({ queryKey: nk(project, 'generators'), queryFn: getGens })
    const s = useSolveSettled(project, f.solveId, [{ key: 'generators', data: q.data }])
    return { ...s, fresh: f.fresh, data: q.data }
  }
  beforeEach(() => { gens = [{ name: 'g', p_nom_opt: 1 }]; getGens.mockClear() })

  it('refetches the lists on first sight of a fresh solve, then is current', async () => {
    vi.mocked(simulationApi.getStatus).mockResolvedValue(st('fresh', 10) as never)
    const { result } = renderHook(() => host(), { wrapper })
    expect(result.current.current).toBe(false)
    await waitFor(() => expect(result.current.current).toBe(true))
    expect(getGens).toHaveBeenCalledTimes(2)              // mount + the settle refetch
    expect(result.current.freshSince).toBe(0)             // first sight: a warm cache is valid
  })
  it('an edit (the list changes) ends "current" at once, before the status poll says so', async () => {
    vi.mocked(simulationApi.getStatus).mockResolvedValue(st('fresh', 10) as never)
    const { result } = renderHook(() => host(), { wrapper })
    await waitFor(() => expect(result.current.current).toBe(true))
    gens = [{ name: 'g', p_nom_opt: 1 }, { name: 'h', p_nom_opt: 0 }]
    await act(async () => { await qc.invalidateQueries({ queryKey: nk('p', 'generators') }) })
    await waitFor(() => expect(result.current.data).toHaveLength(2))
    expect(result.current.current).toBe(false)
    expect(result.current.fresh).toBe(true)                // the poll has not caught up
  })
  it('a refetch that brings identical data (another observer, a stale refetch) keeps it current', async () => {
    vi.mocked(simulationApi.getStatus).mockResolvedValue(st('fresh', 10) as never)
    const { result } = renderHook(() => host(), { wrapper })
    await waitFor(() => expect(result.current.current).toBe(true))
    gens = [{ name: 'g', p_nom_opt: 1 }]                   // new objects, same content
    await act(async () => { await qc.invalidateQueries({ queryKey: nk('p', 'generators') }) })
    expect(getGens).toHaveBeenCalledTimes(3)
    expect(result.current.current).toBe(true)
  })
  it('a new solve with no non-fresh poll between (fresh A → fresh B) refetches and moves freshSince', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.mocked(simulationApi.getStatus).mockResolvedValue(st('fresh', 10) as never)
    const { result } = renderHook(() => host(), { wrapper })
    await waitFor(() => expect(result.current.current).toBe(true))
    const calls = getGens.mock.calls.length
    gens = [{ name: 'g', p_nom_opt: 7 }]
    vi.mocked(simulationApi.getStatus).mockResolvedValue(st('fresh', 12, 2) as never)
    await act(async () => { await vi.advanceTimersByTimeAsync(STATUS_POLL_MS + 50) })
    await waitFor(() => expect(result.current.data?.[0].p_nom_opt).toBe(7))
    expect(getGens.mock.calls.length).toBe(calls + 1)
    await waitFor(() => expect(result.current.current).toBe(true))
    expect(result.current.freshSince).toBeGreaterThan(0)
  })
  it('not fresh → never current', async () => {
    vi.mocked(simulationApi.getStatus).mockResolvedValue(st('none', 10) as never)
    const { result } = renderHook(() => host(), { wrapper })
    await waitFor(() => expect(simulationApi.getStatus).toHaveBeenCalled())
    await act(async () => { await new Promise(r => setTimeout(r, 20)) })
    expect(result.current.current).toBe(false)
  })
  it('a project switch starts over: the new project\'s first fresh answer is a first sight', async () => {
    vi.mocked(simulationApi.getStatus).mockResolvedValue(st('fresh', 10) as never)
    const { result, rerender } = renderHook(({ p }) => host(p), { wrapper, initialProps: { p: 'A' } })
    await waitFor(() => expect(result.current.current).toBe(true))
    vi.mocked(simulationApi.getStatus).mockResolvedValue(st('fresh', 99) as never)
    rerender({ p: 'B' })
    expect(result.current.current).toBe(false)
    await waitFor(() => expect(result.current.current).toBe(true))
    expect(result.current.freshSince).toBe(0)
  })
})
