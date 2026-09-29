// Phase 2 plan Tasks 5.2–5.3 / spec §6.1, §6.3: the 3D view's per-asset
// results map — freshness rules, the per-class map, cache sharing with the
// other canvases, chunk boundaries. The host composes the status poll, the
// settle signal and the hook the way SiteCanvas does.
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { resultsApi, simulationApi, type TSRange } from '../api/simulation'
import { networkApi } from '../api/network'
import { nk } from '../utils/queryKeys'
import { useDispatchFresh, useSolveSettled, STATUS_POLL_MS } from './useDispatchFresh'
import { useSiteResults, type SiteResultObject } from './useSiteResults'

vi.mock('../api/simulation')
vi.mock('../api/network')
// Small chunks, so a 60-snapshot horizon has chunk boundaries.
vi.mock('../pages/results/chunking', async orig => ({ ...(await orig<object>()), chooseChunk: (_: number, total: number) => Math.min(24, Math.max(1, total)) }))

const TOTAL = 60
/** A range-aware series: column → value at row r. The probe ({0,0}) carries columns + range.total. */
function series(cols: Record<string, (r: number) => number>, stamp = 0) {
  return vi.fn(async (_src?: unknown, range?: TSRange) => {
    const from = range?.from ?? 0, to = range?.to ?? TOTAL - 1
    const rows = Array.from({ length: to - from + 1 }, (_, k) => from + k)
    return { index: rows.map(r => `t${r}`), columns: Object.keys(cols), data: rows.map(r => Object.values(cols).map(f => f(r) + stamp)), range: { from, to, total: TOTAL } }
  })
}

const LISTS = {
  buses: [{ name: 'B' }, { name: 'Grid' }, { name: 'H2' }, { name: 'LV' }],
  generators: [{ name: 'PV', bus: 'B', carrier: 'solar', p_nom: 20, p_nom_opt: 20 }],
  storage_units: [{ name: 'BESS 1', bus: 'B', carrier: 'battery', p_nom: 40, max_hours: 2 }],
  stores: [{ name: 'H2 tank', bus: 'B', carrier: 'H2', e_nom: 120 }],
  loads: [{ name: 'Hall A', bus: 'B', p_set: 5 }],
  transformers: [{ name: 'TR1', bus0: 'B', bus1: 'LV', s_nom: 50 }],
  lines: [{ name: 'L1', bus0: 'B', bus1: 'Grid', s_nom: 100 }],
  links: [{ name: 'Electrolyser', bus0: 'B', bus1: 'H2', carrier: 'electrolysis', p_nom: 10 }],
}
const KEYS = Object.keys(LISTS) as (keyof typeof LISTS)[]
const ALL: SiteResultObject[] = [
  { type: 'Generator', name: 'PV', kind: 'pv', bus: 'B' },
  { type: 'StorageUnit', name: 'BESS 1', kind: 'bess', bus: 'B' },
  { type: 'Store', name: 'H2 tank', kind: 'h2store', bus: 'B' },
  { type: 'Load', name: 'Hall A', kind: 'load', bus: 'B' },
  { type: 'Link', name: 'Electrolyser', kind: 'electrolyser', bus: 'B' },
  { type: 'Line', name: 'L1', kind: 'feeder', bus: 'B' },
  { type: 'Transformer', name: 'TR1', kind: 'transformer', bus: 'B' },
]
const fetchers: Record<string, ReturnType<typeof vi.fn>> = {}

let qc: QueryClient
const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>
let lists: typeof LISTS

function installSeries(stamp = 0) {
  fetchers.generators = series({ PV: r => r / 2, 'X@2030': () => 99 }, stamp)
  fetchers.storage_dispatch = series({ 'BESS 1': () => 10 }, stamp)
  fetchers.storage = series({ 'BESS 1': () => 80 }, stamp)
  fetchers.store_dispatch = series({ 'H2 tank': () => -2 }, stamp)
  fetchers.store_energy = series({ 'H2 tank': () => 60 }, stamp)
  fetchers.loads = series({ 'Hall A': () => 4 }, stamp)
  fetchers.links = series({ Electrolyser: () => 8 }, stamp)
  fetchers.lines = series({ L1: () => 30, TR1: () => 999 }, stamp)
  fetchers.transformers = series({ TR1: () => -20 }, stamp)
  vi.mocked(resultsApi.getGeneratorResults).mockImplementation(fetchers.generators as never)
  vi.mocked(resultsApi.getStorageDispatchResults).mockImplementation(fetchers.storage_dispatch as never)
  vi.mocked(resultsApi.getStorageResults).mockImplementation(fetchers.storage as never)
  vi.mocked(resultsApi.getStoreDispatchResults).mockImplementation(fetchers.store_dispatch as never)
  vi.mocked(resultsApi.getStoreEnergyResults).mockImplementation(fetchers.store_energy as never)
  vi.mocked(resultsApi.getLoadResults).mockImplementation(fetchers.loads as never)
  vi.mocked(resultsApi.getLinkResults).mockImplementation(fetchers.links as never)
  vi.mocked(resultsApi.getLineResults).mockImplementation(fetchers.lines as never)
  vi.mocked(resultsApi.getTransformerResults).mockImplementation(fetchers.transformers as never)
}

const status = (dispatch: string, objective = 1, solve_time = 1) => ({ status: 'completed', running: false, dispatch, objective, solve_time })

beforeEach(() => {
  qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  lists = structuredClone(LISTS)
  installSeries()
  vi.mocked(simulationApi.getStatus).mockReset().mockResolvedValue(status('fresh') as never)
  vi.mocked(simulationApi.getSolverConfig).mockReset().mockResolvedValue({ multi_investment_periods: true, load_scalers: { 2030: 1.25 } } as never)
  vi.mocked(networkApi.getInvestmentPeriods).mockReset().mockResolvedValue({ periods: [2026, 2030], weightings: [] } as never)
  vi.mocked(networkApi.getLoadProfiles).mockReset().mockResolvedValue({ 'Hall A': { has_profile: true, peak: 8, section: 'x', bus: 'B', rows: 1, start: null, end: null, mean: 1, sum: 1 } } as never)
  vi.mocked(networkApi.listVintageResults).mockReset().mockResolvedValue({ results: {} } as never)
  const get = (k: keyof typeof LISTS) => vi.fn(async () => structuredClone(lists[k]))
  vi.mocked(networkApi.getBuses).mockImplementation(get('buses') as never)
  vi.mocked(networkApi.getGenerators).mockImplementation(get('generators') as never)
  vi.mocked(networkApi.getStorageUnits).mockImplementation(get('storage_units') as never)
  vi.mocked(networkApi.getStores).mockImplementation(get('stores') as never)
  vi.mocked(networkApi.getLoads).mockImplementation(get('loads') as never)
  vi.mocked(networkApi.getTransformers).mockImplementation(get('transformers') as never)
  vi.mocked(networkApi.getLines).mockImplementation(get('lines') as never)
  vi.mocked(networkApi.getLinks).mockImplementation(get('links') as never)
})
afterEach(() => { vi.useRealTimers() })

const LIST_FN: Record<string, () => Promise<unknown>> = {
  buses: () => networkApi.getBuses(), generators: () => networkApi.getGenerators(), storage_units: () => networkApi.getStorageUnits(),
  stores: () => networkApi.getStores(), loads: () => networkApi.getLoads(), transformers: () => networkApi.getTransformers(),
  lines: () => networkApi.getLines(), links: () => networkApi.getLinks(),
}

function useHost({ eye = true, idx = 0, objects = ALL, source = 'lopf' }: { eye?: boolean; idx?: number; objects?: SiteResultObject[]; source?: 'lopf' | 'ac_pf' }) {
  const f = useDispatchFresh('p')
  const qs = KEYS.map(k => useQuery({ queryKey: nk('p', k), queryFn: LIST_FN[k] }))  // fixed order: hooks rule holds
  const s = useSolveSettled('p', f.solveId, KEYS.map((k, i) => ({ key: k, data: qs[i].data })))
  const d = (k: keyof typeof LISTS) => (qs[KEYS.indexOf(k)].data ?? []) as never
  return useSiteResults({
    project: 'p', enabled: eye, current: f.fresh && s.current, freshSince: s.freshSince, idx, source, objects,
    components: { generators: d('generators'), storageUnits: d('storage_units'), stores: d('stores'), loads: d('loads'), transformers: d('transformers'), lines: d('lines'), links: d('links') },
  })
}
const chunkCalls = () => Object.values(fetchers).reduce((a, f) => a + f.mock.calls.filter(c => (c[1] as TSRange | undefined)?.to !== 0).length, 0)

describe('useSiteResults — freshness (Task 5.2)', () => {
  it('(pin) Eye off: empty map and no result request', async () => {
    const { result } = renderHook(() => useHost({ eye: false }), { wrapper })
    await act(async () => { await new Promise(r => setTimeout(r, 30)) })
    expect(result.current.states.size).toBe(0)
    expect(Object.values(fetchers).every(f => f.mock.calls.length === 0)).toBe(true)
  })
  it('empty while dispatch is not fresh', async () => {
    vi.mocked(simulationApi.getStatus).mockResolvedValue(status('stale') as never)
    const { result } = renderHook(() => useHost({}), { wrapper })
    await waitFor(() => expect(simulationApi.getStatus).toHaveBeenCalled())
    await act(async () => { await new Promise(r => setTimeout(r, 30)) })
    expect(result.current.states.size).toBe(0)
  })
  it('drop on edit: the map empties as soon as a list the site reads refetches with new data, before the status poll', async () => {
    const { result } = renderHook(() => useHost({}), { wrapper })
    await waitFor(() => expect(result.current.states.size).toBe(7))
    await waitFor(() => expect(vi.mocked(networkApi.getGenerators).mock.calls.length).toBeGreaterThanOrEqual(2))   // settled
    lists.generators[0].p_nom = 25
    await act(async () => { await qc.invalidateQueries({ queryKey: nk('p', 'generators') }) })
    await waitFor(() => expect(result.current.states.size).toBe(0))
    expect(qc.getQueryData(nk('p', 'simulationStatus'))).toMatchObject({ dispatch: 'fresh' })   // the status never said otherwise
  })
  it('(pin) a placement write (objects move, lists untouched) keeps the map', async () => {
    const { result, rerender } = renderHook(p => useHost(p), { wrapper, initialProps: { objects: ALL } })
    await waitFor(() => expect(result.current.states.size).toBe(7))
    rerender({ objects: ALL.map(o => ({ ...o })) })
    expect(result.current.states.size).toBe(7)
  })
  it('rejects chunks fetched before a re-solve: stale chunk → empty; the new chunk → filled', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const { result } = renderHook(() => useHost({}), { wrapper })
    await waitFor(() => expect(result.current.states.get('Generator:PV')).toBeDefined())
    // Re-solve: the status flips none → fresh (a new solve); the chunk refetch is held back.
    vi.mocked(simulationApi.getStatus).mockResolvedValue(status('none') as never)
    await act(async () => { await vi.advanceTimersByTimeAsync(STATUS_POLL_MS + 50) })
    expect(result.current.states.size).toBe(0)
    let release!: () => void
    const gate = new Promise<void>(r => { release = r })
    installSeries(1000)
    const slow = fetchers.generators as unknown as (s: unknown, r?: TSRange) => Promise<unknown>
    vi.mocked(resultsApi.getGeneratorResults).mockImplementation((async (s: unknown, r: TSRange) => { await gate; return slow(s, r) }) as never)
    vi.mocked(simulationApi.getStatus).mockResolvedValue(status('fresh', 2, 2) as never)
    await act(async () => { await vi.advanceTimersByTimeAsync(STATUS_POLL_MS + 50) })
    await waitFor(() => expect(vi.mocked(networkApi.getGenerators).mock.calls.length).toBeGreaterThanOrEqual(3))   // lists re-settled
    await act(async () => { await vi.advanceTimersByTimeAsync(50) })
    expect(result.current.states.get('Generator:PV')).toBeUndefined()   // the old chunk is not shown as current
    await act(async () => { release() })
    await waitFor(() => expect((result.current.states.get('Generator:PV') as { mw: number } | undefined)?.mw).toBe(1000))
  })
  it('mounting with a warm, valid cache and a fresh status: filled from the cache before any request answers', async () => {
    const first = renderHook(() => useHost({}), { wrapper })
    await waitFor(() => expect(first.result.current.states.size).toBe(7))
    first.unmount()
    for (const f of Object.values(fetchers)) f.mockImplementation(() => new Promise(() => {}))
    vi.mocked(simulationApi.getStatus).mockReturnValue(new Promise(() => {}) as never)
    for (const k of Object.keys(LIST_FN)) vi.mocked(networkApi[({ buses: 'getBuses', generators: 'getGenerators', storage_units: 'getStorageUnits', stores: 'getStores', loads: 'getLoads', transformers: 'getTransformers', lines: 'getLines', links: 'getLinks' } as const)[k as keyof typeof LISTS]]).mockReturnValue(new Promise(() => {}) as never)
    const again = renderHook(() => useHost({}), { wrapper })
    expect(again.result.current.states.size).toBe(7)
  })
  it('an index beyond the horizon uses the last row', async () => {
    const { result } = renderHook(() => useHost({ idx: 9999 }), { wrapper })
    await waitFor(() => expect(result.current.states.get('Generator:PV')).toBeDefined())
    expect((result.current.states.get('Generator:PV') as { mw: number }).mw).toBe((TOTAL - 1) / 2)
    expect(result.current.idx).toBe(TOTAL - 1)
  })
})

describe('useSiteResults — the per-asset map (Task 5.3)', () => {
  it('holds each class\'s state at the snapshot', async () => {
    const { result } = renderHook(() => useHost({ idx: 4 }), { wrapper })
    await waitFor(() => expect(result.current.states.size).toBe(7))
    const m = result.current.states
    expect(m.get('Generator:PV')).toEqual({ kind: 'output', mw: 2, cap: 20 })
    expect(m.get('StorageUnit:BESS 1')).toEqual({ kind: 'storage', mw: 10, energy: 80, energyCap: 80 })
    expect(m.get('Store:H2 tank')).toEqual({ kind: 'storage', mw: -2, energy: 60, energyCap: 120 })
    expect(m.get('Load:Hall A')).toEqual({ kind: 'load', mw: 4, peak: 10 })        // profile peak 8 × scaler 1.25
    expect(m.get('Link:Electrolyser')).toEqual({ kind: 'link', mw: 8, cap: 10 })
    expect(m.get('Line:L1')).toEqual({ kind: 'branch', p0: 30, cap: 100, unit: 'MVA', bus0: 'B', bus1: 'Grid' })
    expect(m.get('Transformer:TR1')).toEqual({ kind: 'branch', p0: -20, cap: 50, unit: 'MVA', bus0: 'B', bus1: 'LV' })  // never the Lines series' TR1 (999)
    expect(result.current.iso).toBe('t4')
  })
  it('a feeder Link is a branch (flow and loading), with MW as its unit', async () => {
    lists.links.push({ name: 'DC', bus0: 'Grid', bus1: 'B', carrier: 'DC', p_nom: 20 })
    fetchers.links = series({ Electrolyser: () => 8, DC: () => -5 })
    vi.mocked(resultsApi.getLinkResults).mockImplementation(fetchers.links as never)
    const { result } = renderHook(() => useHost({ objects: [...ALL, { type: 'Link', name: 'DC', kind: 'feeder', bus: 'B' }] }), { wrapper })
    await waitFor(() => expect(result.current.states.get('Link:DC')).toBeDefined())
    expect(result.current.states.get('Link:DC')).toEqual({ kind: 'branch', p0: -5, cap: 20, unit: 'MW', bus0: 'Grid', bus1: 'B' })
  })
  it('only the series the site needs are fetched (no Store → no store calls)', async () => {
    const { result } = renderHook(() => useHost({ objects: ALL.filter(o => o.type !== 'Store') }), { wrapper })
    await waitFor(() => expect(result.current.states.size).toBe(6))
    expect(fetchers.store_dispatch).not.toHaveBeenCalled()
    expect(fetchers.store_energy).not.toHaveBeenCalled()
  })
  it('shares the cache with the other canvases (same keys), and does not refetch the probe', async () => {
    const { result, unmount } = renderHook(() => useHost({}), { wrapper })
    await waitFor(() => expect(result.current.states.size).toBe(7))
    expect(qc.getQueryCache().find({ queryKey: nk('p', 'results', 'generators', 'lopf', 0), exact: true })).toBeDefined()
    expect(qc.getQueryCache().find({ queryKey: nk('p', 'results', 'generators', 'lopf', 'probe'), exact: true })).toBeDefined()
    const probes = fetchers.generators.mock.calls.filter(c => (c[1] as TSRange).to === 0).length
    unmount()
    renderHook(() => useHost({}), { wrapper })
    await act(async () => { await new Promise(r => setTimeout(r, 30)) })
    expect(fetchers.generators.mock.calls.filter(c => (c[1] as TSRange).to === 0).length).toBe(probes)
  })
  it('(pin) the transformer chunk key does not collide with LoadFlow\'s window key', async () => {
    const { result } = renderHook(() => useHost({}), { wrapper })
    await waitFor(() => expect(result.current.states.size).toBe(7))
    const keys = qc.getQueryCache().findAll({ queryKey: nk('p', 'results', 'transformers') }).map(q => q.queryKey)
    expect(keys.every(k => k.length === nk('p', 'results', 'transformers', 'lopf', 0).length)).toBe(true)
    expect(keys).not.toContainEqual(nk('p', 'results', 'transformers', 'lopf', 0, 23))
  })
  it('an object missing from the columns has no state (not zero); a stray X@2030 column is ignored', async () => {
    const { result } = renderHook(() => useHost({ objects: [...ALL, { type: 'Generator', name: 'Ghost', kind: 'pv', bus: 'B' }, { type: 'Generator', name: 'X', kind: 'pv', bus: 'B' }] }), { wrapper })
    await waitFor(() => expect(result.current.states.size).toBe(7))
    expect(result.current.states.has('Generator:Ghost')).toBe(false)
    expect(result.current.states.has('Generator:X')).toBe(false)
  })
  it('at a chunk boundary the last state is held while the next chunk loads, and the next chunk is prefetched near the end', async () => {
    const { result, rerender } = renderHook(p => useHost(p), { wrapper, initialProps: { idx: 22 } })
    await waitFor(() => expect(result.current.states.size).toBe(7))
    // Near the end of chunk 0 (rows 0–23): chunk 24 is prefetched.
    await waitFor(() => expect(fetchers.generators.mock.calls.some(c => (c[1] as TSRange).from === 24)).toBe(true))
    // Block chunk 48 and jump there: the previous state is held, not emptied.
    fetchers.generators.mockImplementation(async (s: unknown, r?: TSRange) => { if (r?.from === 48) return new Promise(() => {}); return series({ PV: x => x / 2 })(s, r) })
    rerender({ idx: 50 })
    expect(result.current.states.get('Generator:PV')).toEqual({ kind: 'output', mw: 11, cap: 20 })
    expect(chunkCalls()).toBeGreaterThan(0)
  })
})

describe('useSiteResults — WP5 gate scenarios', () => {
  const PV = (r: { states: Map<string, unknown> }) => r.states.get('Generator:PV') as { mw: number; cap: number | null } | undefined
  it('R1: reopened after a re-solve, the previous solve\'s (invalidated) chunks are never shown as current', async () => {
    const first = renderHook(() => useHost({}), { wrapper })
    await waitFor(() => expect(first.result.current.states.size).toBe(7))
    first.unmount()
    qc.setQueryData(nk('p', 'simulationStatus'), status('fresh', 2, 2))
    vi.mocked(simulationApi.getStatus).mockResolvedValue(status('fresh', 2, 2) as never)
    await act(async () => { await qc.invalidateQueries({ queryKey: nk('p', 'results'), refetchType: 'none' }) })   // useJobTerminalInvalidation
    let release!: () => void
    const gate = new Promise<void>(r => { release = r })
    installSeries(1000)
    for (const f of Object.values(fetchers)) { const g = f.getMockImplementation()! as (...a: unknown[]) => Promise<unknown>; f.mockImplementation(async (...a: unknown[]) => { await gate; return g(...a) }) }
    const again = renderHook(() => useHost({}), { wrapper })
    expect(PV(again.result.current)).toBeUndefined()
    await act(async () => { await new Promise(r => setTimeout(r, 30)) })
    expect(PV(again.result.current)).toBeUndefined()
    await act(async () => { release() })
    await waitFor(() => expect(PV(again.result.current)?.mw).toBe(1000))
  })
  it('R2: a stale chunk whose refetch keeps failing is invalidated once per solve, not in a loop', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    qc = new QueryClient({ defaultOptions: { queries: { retry: 1, retryDelay: 1000 } } })
    const { result } = renderHook(() => useHost({}), { wrapper })
    await waitFor(() => expect(result.current.states.size).toBe(7))
    vi.mocked(resultsApi.getGeneratorResults).mockImplementation((async () => { throw new Error('500') }) as never)
    vi.mocked(simulationApi.getStatus).mockResolvedValue(status('fresh', 2, 2) as never)
    await act(async () => { await vi.advanceTimersByTimeAsync(STATUS_POLL_MS + 50) })
    const before = vi.mocked(resultsApi.getGeneratorResults).mock.calls.length
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
    expect(vi.mocked(resultsApi.getGeneratorResults).mock.calls.length - before).toBeLessThanOrEqual(6)
  })
  it('R3: switching the source never holds the other source\'s map', async () => {
    const { result, rerender } = renderHook(p => useHost(p), { wrapper, initialProps: { source: 'lopf' as 'lopf' | 'ac_pf' } })
    await waitFor(() => expect(result.current.states.size).toBe(7))
    for (const f of Object.values(fetchers)) f.mockImplementation(() => new Promise(() => {}))
    rerender({ source: 'ac_pf' })
    expect(result.current.states.size).toBe(0)
  })
  it('R4: a series that answers null (204) drops only its class', async () => {
    vi.mocked(resultsApi.getLoadResults).mockImplementation((async () => null) as never)
    const { result } = renderHook(() => useHost({}), { wrapper })
    await waitFor(() => expect(result.current.states.size).toBe(6))
    expect(result.current.states.has('Load:Hall A')).toBe(false)
  })
  it('R5: Eye off then on elsewhere on the timeline: the old snapshot is not held', async () => {
    const { result, rerender } = renderHook(p => useHost(p), { wrapper, initialProps: { eye: true, idx: 2 } })
    await waitFor(() => expect(result.current.states.size).toBe(7))
    rerender({ eye: false, idx: 2 })
    fetchers.generators.mockImplementation(async () => new Promise(() => {}))
    rerender({ eye: false, idx: 50 })
    rerender({ eye: true, idx: 50 })
    expect(PV(result.current)).toBeUndefined()
  })
  it('R6: a re-solve with the view open refetches the vintage breakdown (period-effective capacity)', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.mocked(networkApi.listVintageResults).mockResolvedValue({ results: { Generator: { PV: { initial_capacity: 20, periods: [{ build_year: 2030, p_nom_opt: 10 }] } } } } as never)
    const { result } = renderHook(() => useHost({}), { wrapper })
    await waitFor(() => expect(PV(result.current)?.cap).toBe(30))
    vi.mocked(networkApi.listVintageResults).mockResolvedValue({ results: { Generator: { PV: { initial_capacity: 20, periods: [{ build_year: 2030, p_nom_opt: 40 }] } } } } as never)
    installSeries(1000)
    vi.mocked(simulationApi.getStatus).mockResolvedValue(status('fresh', 2, 2) as never)
    await act(async () => { await vi.advanceTimersByTimeAsync(STATUS_POLL_MS + 50) })
    await waitFor(() => expect(PV(result.current)?.mw).toBe(1000))
    expect(PV(result.current)?.cap).toBe(60)
  })
  it('multi-period: a vintage counts only from its build year (the hook wires the period in)', async () => {
    const periodsOf = (r: number) => (r < 30 ? 2026 : 2030)
    fetchers.generators = vi.fn(async (_s?: unknown, range?: TSRange) => {
      const from = range?.from ?? 0, to = range?.to ?? TOTAL - 1
      const rows = Array.from({ length: to - from + 1 }, (_, k) => from + k)
      return { index: rows.map(r => `t${r}`), periods: rows.map(periodsOf), columns: ['PV'], data: rows.map(() => [1]), range: { from, to, total: TOTAL } }
    })
    vi.mocked(resultsApi.getGeneratorResults).mockImplementation(fetchers.generators as never)
    vi.mocked(networkApi.listVintageResults).mockResolvedValue({ results: { Generator: { PV: { initial_capacity: 20, periods: [{ build_year: 2030, p_nom_opt: 10 }] } } } } as never)
    const a = renderHook(() => useHost({ idx: 5 }), { wrapper })
    await waitFor(() => expect(PV(a.result.current)?.cap).toBe(20))
    a.unmount()
    const b = renderHook(() => useHost({ idx: 40 }), { wrapper })
    await waitFor(() => expect(PV(b.result.current)?.cap).toBe(30))
  })
  it('R7: an edit made while the view was closed: a stale cached "fresh" is not trusted before the status answers', async () => {
    const first = renderHook(() => useHost({}), { wrapper })
    await waitFor(() => expect(first.result.current.states.size).toBe(7))
    first.unmount()
    lists.generators[0].p_nom_opt = 5
    await act(async () => { await qc.refetchQueries({ queryKey: nk('p', 'generators') }) })
    // The cached status answer is old: nobody polled it while the view was closed.
    const st = qc.getQueryCache().find({ queryKey: nk('p', 'simulationStatus') })!
    st.setState({ dataUpdatedAt: Date.now() - 60_000 })
    let release!: () => void
    vi.mocked(simulationApi.getStatus).mockImplementation(() => new Promise(r => { release = () => r(status('none') as never) }) as never)
    const again = renderHook(() => useHost({}), { wrapper })
    await act(async () => { await new Promise(r => setTimeout(r, 50)) })
    expect(again.result.current.states.size).toBe(0)
    await act(async () => { release() })
    await waitFor(() => expect(again.result.current.states.size).toBe(0))
  })
})
