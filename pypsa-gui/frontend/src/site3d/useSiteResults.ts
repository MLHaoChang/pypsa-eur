// The 3D view's results: one map per snapshot, `"Class:name"` → AssetState,
// for the site's objects only (Phase 2 spec §6.1, §6.3; plan Tasks 5.2–5.3).
// No three.
//
// Series come through the canvases' chunked machinery with the same query
// keys, so the 3D view shares their cache. Freshness (spec §6.3):
//  1. the caller's own status poll (useDispatchFresh) and
//  2. the settle signal (useSolveSettled: an edit — a list refetch with new
//     data — ends it at once) combine into `current`: not current → empty;
//  3. a chunk fetched before `freshSince` (a re-solve) is rejected and
//     refetched, so the previous solve's values never show as current.
// While the chunk for the snapshot is loading, the last map is held (no
// flicker to neutral), and the next chunk is prefetched near a boundary.
import { useEffect, useMemo, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { resultsApi, type ResultSource, type TSRange } from '../api/simulation'
import { networkApi } from '../api/network'
import { simulationApi } from '../api/simulation'
import type { Generator, Line, Link, Load, StorageUnit, Store, Transformer } from '../api/types'
import { useChunkedSeriesMeta, type ChunkedSeriesMeta } from '../components/CanvasResultsContext'
import { horizonOf, localRow } from '../pages/results/chunking'
import type { TSPayload } from '../pages/results/shared'
import { nk } from '../utils/queryKeys'
import { capOf, loadPeak, periodAt, periodEffectiveCap, type VintageResults } from './capacity'
import type { AssetState } from './resultStyle'

export interface SiteResultObject { type: string; name: string; kind: string; bus: string }

export interface SiteComponents {
  generators: Generator[]; storageUnits: StorageUnit[]; stores: Store[]; loads: Load[]
  transformers: Transformer[]; lines: Line[]; links: Link[]
}

export interface SiteResultsInput {
  project: string | null
  /** The Eye (resultsOverlayEnabled): off → nothing is fetched. */
  enabled: boolean
  /** Dispatch fresh and the lists settled and unchanged (useDispatchFresh + useSolveSettled). */
  current: boolean
  /** Chunks fetched before this (ms) belong to an earlier solve. */
  freshSince: number
  idx: number
  source: ResultSource
  objects: readonly SiteResultObject[]
  components: SiteComponents
}

export interface SiteResults {
  states: Map<string, AssetState>
  /** The snapshot shown (clamped to the horizon) and its timestamp. */
  idx: number
  iso: string
}

type Fetch = (source: ResultSource, range?: TSRange) => Promise<TSPayload | null>
const SERIES = {
  generators: resultsApi.getGeneratorResults,
  storage_dispatch: resultsApi.getStorageDispatchResults,
  storage: resultsApi.getStorageResults,
  store_dispatch: resultsApi.getStoreDispatchResults,
  store_energy: resultsApi.getStoreEnergyResults,
  loads: resultsApi.getLoadResults,
  links: resultsApi.getLinkResults,
  lines: resultsApi.getLineResults,
  // Transformers have their own series (spec E13); the schematic looks them up in the Lines map, a bug not copied.
  transformers: resultsApi.getTransformerResults,
} as const satisfies Record<string, Fetch>
type SeriesName = keyof typeof SERIES
const NEEDS: Record<string, SeriesName[]> = {
  Generator: ['generators'], StorageUnit: ['storage_dispatch', 'storage'], Store: ['store_dispatch', 'store_energy'],
  Load: ['loads'], Link: ['links'], Line: ['lines'], Transformer: ['transformers'],
}
const NAMES = Object.keys(SERIES) as SeriesName[]

const EMPTY: SiteResults = { states: new Map(), idx: 0, iso: '' }

function rowOf(p: TSPayload | null | undefined, idx: number): Map<string, number> | null {
  const r = localRow(p, idx)
  if (!p || r < 0 || r >= p.data.length) return null
  const m = new Map<string, number>()
  p.columns.forEach((c, i) => { const v = p.data[r][i]; if (Number.isFinite(v)) m.set(c, v) })
  return m
}

export function useSiteResults(input: SiteResultsInput): SiteResults {
  const { project, enabled, current, freshSince, idx, source, objects, components } = input
  const active = enabled && current && !!project
  const classes = useMemo(() => new Set(objects.map(o => o.type)), [objects])
  const needed = useMemo(() => new Set(NAMES.filter(n => [...classes].some(c => NEEDS[c]?.includes(n)))), [classes])

  // Fixed call order (one per series); a series the site does not need is disabled.
  const meta = {} as Record<SeriesName, ChunkedSeriesMeta>
  for (const n of NAMES) {
    // eslint-disable-next-line react-hooks/rules-of-hooks -- NAMES is a constant list
    meta[n] = useChunkedSeriesMeta(n, SERIES[n], { project, source, idx, enabled: active && needed.has(n), prefetchNext: true })
  }

  const { data: profiles } = useQuery({
    queryKey: nk(project, 'load_profiles'), queryFn: networkApi.getLoadProfiles,
    enabled: active && classes.has('Load'), staleTime: 30_000,
  })
  const { data: solverConfig } = useQuery({
    queryKey: nk(project, 'solverConfig'), queryFn: simulationApi.getSolverConfig,
    enabled: active && classes.has('Load'),
  })
  const { data: periods } = useQuery({
    queryKey: nk(project, 'investmentPeriods'), queryFn: networkApi.getInvestmentPeriods,
    enabled: active && classes.has('Load') && !!solverConfig?.multi_investment_periods,
  })
  // The vintage breakdown changes with every solve and nothing invalidates
  // it: it counts only when fetched after the latest solve (like a chunk).
  const vintageKey = nk(project, 'vintage_results')
  const { data: vintage, dataUpdatedAt: vintageAt } = useQuery({
    queryKey: vintageKey, queryFn: () => networkApi.listVintageResults(), enabled: active,
  })
  const vintageStale = active && vintageAt < freshSince

  // Rule 3: a chunk from before the latest solve (fetched earlier, or
  // invalidated by the finished job) is not shown. A chunk fetched earlier
  // is refetched — once per solve, so a refetch that keeps failing does not
  // loop.
  const qc = useQueryClient()
  const invalidated = (m: ChunkedSeriesMeta) => !!qc.getQueryState(m.queryKey)?.isInvalidated
  const invSig = NAMES.map(n => (invalidated(meta[n]) ? 1 : 0)).join('')
  const staleKeys = NAMES.filter(n => needed.has(n) && meta[n].data && meta[n].dataUpdatedAt < freshSince && !meta[n].isFetching)
    .map(n => JSON.stringify(meta[n].queryKey))
  if (vintageStale) staleKeys.push(JSON.stringify(vintageKey))
  const staleSig = active ? staleKeys.join('|') : ''
  const refetched = useRef(new Set<string>())
  useEffect(() => {
    if (!staleSig) return
    for (const k of staleSig.split('|')) {
      const once = `${freshSince}|${k}`
      if (refetched.current.has(once)) continue
      refetched.current.add(once)
      void qc.invalidateQueries({ queryKey: JSON.parse(k) as unknown[], exact: true })
    }
  }, [staleSig, freshSince, qc])

  const held = useRef<{ key: string; value: SiteResults } | null>(null)
  const payloads = NAMES.map(n => meta[n].data)
  const stamps = NAMES.map(n => meta[n].dataUpdatedAt)

  const value = useMemo((): SiteResults | 'pending' => {
    if (!active) return EMPTY
    const valid = (n: SeriesName): TSPayload | null => {
      const m = meta[n]
      return m.data && m.dataUpdatedAt >= freshSince && !invalidated(m) ? m.data : null
    }
    // Per series: its payload, or absent (answered null / failed: that class
    // shows nothing), or loading (hold the last map).
    const want = [...needed]
    let loading = vintageStale
    const payload = {} as Record<SeriesName, TSPayload | null>
    for (const n of want) {
      payload[n] = valid(n)
      const m = meta[n]
      const absent = !payload[n] && (m.absent || (m.data === null && !m.isFetching) || (m.isError && !m.isFetching))
      if (!payload[n] && !absent) loading = true
    }
    if (loading) return 'pending'
    const present = want.filter(n => payload[n])
    const horizon = horizonOf(present.map(n => payload[n]))
    if (horizon === 0) return { states: new Map(), idx: 0, iso: '' }
    const at = Math.max(0, Math.min(idx, horizon - 1))
    const rows = {} as Record<SeriesName, Map<string, number> | null>
    for (const n of want) {
      rows[n] = payload[n] ? rowOf(payload[n], at) : null
      if (payload[n] && !rows[n]) return 'pending'     // this series' chunk for the snapshot is still loading
    }
    const empty = new Map<string, number>()
    for (const n of want) rows[n] ??= empty
    const any = payload[present[0]]
    const period = periodAt(any, localRow(any, at))
    const vr = vintage?.results as VintageResults | undefined
    const eff = (cls: string, name: string, cap: number | null) => {
      const v = periodEffectiveCap(vr, cls, name, period, cap ?? 0)
      return v > 0 ? v : null
    }
    const by = <T extends { name: string }>(xs: T[]) => new Map(xs.map(x => [x.name, x]))
    const gens = by(components.generators), sus = by(components.storageUnits), sts = by(components.stores), lds = by(components.loads)
    const lks = by(components.links), lns = by(components.lines), trs = by(components.transformers)

    const states = new Map<string, AssetState>()
    for (const o of objects) {
      const key = `${o.type}:${o.name}`
      switch (o.type) {
        case 'Generator': {
          const mw = rows.generators!.get(o.name), g = gens.get(o.name)
          if (mw == null || !g) break
          states.set(key, { kind: 'output', mw, cap: eff('Generator', o.name, capOf(g as never, 'p_nom')) })
          break
        }
        case 'StorageUnit': {
          const mw = rows.storage_dispatch!.get(o.name), s = sus.get(o.name)
          if (mw == null || !s) break
          const p = eff('StorageUnit', o.name, capOf(s as never, 'p_nom'))
          // max_hours 0: no energy rating, so no SoC share.
          states.set(key, { kind: 'storage', mw, energy: rows.storage!.get(o.name) ?? null, energyCap: p != null && (s.max_hours ?? 0) > 0 ? p * s.max_hours : null })
          break
        }
        case 'Store': {
          const mw = rows.store_dispatch!.get(o.name), s = sts.get(o.name)
          if (mw == null || !s) break
          states.set(key, { kind: 'storage', mw, energy: rows.store_energy!.get(o.name) ?? null, energyCap: eff('Store', o.name, capOf(s as never, 'e_nom')) })
          break
        }
        case 'Load': {
          const mw = rows.loads!.get(o.name), l = lds.get(o.name)
          if (mw == null || !l) break
          states.set(key, { kind: 'load', mw, peak: loadPeak(l, profiles?.[o.name], solverConfig, periods?.periods) })
          break
        }
        case 'Link': {
          const p0 = rows.links!.get(o.name), l = lks.get(o.name)
          if (p0 == null || !l) break
          const cap = eff('Link', o.name, capOf(l as never, 'p_nom'))
          states.set(key, o.kind === 'feeder'
            ? { kind: 'branch', p0, cap, unit: 'MW', bus0: l.bus0, bus1: l.bus1 }
            : { kind: 'link', mw: p0, cap })
          break
        }
        case 'Line': case 'Transformer': {
          const p0 = (o.type === 'Line' ? rows.lines : rows.transformers)!.get(o.name)
          const b = o.type === 'Line' ? lns.get(o.name) : trs.get(o.name)
          if (p0 == null || !b) break
          states.set(key, { kind: 'branch', p0, cap: capOf(b as never, 's_nom'), unit: 'MVA', bus0: b.bus0, bus1: b.bus1 })
          break
        }
      }
    }
    return { states, idx: at, iso: any?.index[localRow(any, at)] ?? '' }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- payloads/stamps stand for `meta`
  }, [active, freshSince, idx, needed, objects, components, profiles, solverConfig, periods, vintage, vintageStale, invSig, ...payloads, ...stamps])

  // The hold is for one solve, one source: the Eye going off, a new solve
  // or another source (lopf / ac_pf) never shows a held map.
  const holdKey = `${project}|${freshSince}|${source}`
  if (!active) held.current = null
  if (value !== 'pending') {
    if (active) held.current = { key: holdKey, value }
    return value
  }
  const h = held.current
  return h && h.key === holdKey ? h.value : EMPTY
}
