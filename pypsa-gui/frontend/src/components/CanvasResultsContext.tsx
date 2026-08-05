import { createContext, useContext, useMemo, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import { networkApi } from '../api/network'
import { resultsApi, type TSRange } from '../api/simulation'
import { chooseChunk, chunkBounds } from '../pages/results/chunking'
import type { Generator, Load, Line as LineT, Link as LinkT, StorageUnit, Store } from '../api/types'
import { isRenewableCarrier } from '../pages/results/shared'

// ── Per-snapshot results overlay context ──────────────────────────────────────
// One provider, one shape, two consumers (BusNode and EditableEdge).
// Computes once per snapshot change and exposes:
//   • enabled — should consumers render the overlay at all?
//   • idx / iso — which snapshot is selected (for display)
//   • byBus  — name → {gen, load} (MW) at this snapshot
//   • byLine — name → {p0, loadingPct, sNom} at this snapshot
//
// Pulls from the same /api/results/* endpoints used by the Results panel, so
// no new backend work needed. Aggregates per-bus by walking generators/loads
// and looking up each one in the result DataFrames.

interface OverlayData {
  enabled: boolean
  idx: number
  iso: string
  // The physical quantity currently displayed for line overlays.
  // 'p' is the default. 'q' is only meaningful when AC PF has run; the
  // canvas toggle UI is hidden otherwise (no Q data available).
  kind: 'p' | 'q'
  // Top-level totals at this snapshot (Σ across every carrier on the bus).
  // `byCarrier` breaks the same numbers down per load/generator carrier so
  // a TopologyCanvas can render a stacked-donut bus indicator showing each
  // carrier's share. Backwards-compatible — every existing consumer reads
  // `gen` + `load`; only the new per-carrier indicator looks at byCarrier.
  byBus: Map<string, {
    gen: number
    load: number
    byCarrier: Map<string, { gen: number; load: number }>
  }>
  // p0 is active power (MW), q0 is reactive (MVAr) — null when not loaded.
  // EditableEdge picks `kind === 'q' ? q0 : p0` for the display value.
  byLine: Map<string, { p0: number; q0: number | null; loadingPct: number; sNom: number }>
  // Per-Link active flow at this snapshot. `p0` is signed MW at bus0
  // (positive = flowing bus0 → bus1, i.e. consume at bus0, produce at
  // bus1 × efficiency). `loadingPct` = |p0| / p_nom × 100 (using
  // p_nom_opt when available). `carrier`, `bus0`, `bus1` are surfaced so
  // canvas consumers can colour / label the link without joining back
  // to the Link DataFrame. Empty when no link result data is loaded.
  byLink: Map<string, {
    p0: number
    loadingPct: number
    pNom: number
    carrier: string
    bus0: string
    bus1: string
  }>
  // Summed dispatch per (bus, category) group. Key is `${busName}|${category}`
  // where category is one of 'Thermal' | 'Renewables' | 'Storage' | 'Load'.
  // Value is MW at the current snapshot. Sign convention follows PyPSA's
  // per-component result tables:
  //   • generators (Thermal / Renewables) → positive = injection
  //   • loads (Load)                      → positive = consumption
  //   • storage (StorageUnit + Store)     → positive = discharge, negative = charge
  // Consumers (AssetGroupNode) render an arrow + magnitude based on the sign.
  byAssetGroup: Map<string, number>
  // SoC % per Storage group (0..100). Only populated for keys whose
  // category is 'Storage' and whose underlying assets have a known energy
  // capacity. Aggregated as Σenergy / Σcapacity when a group mixes both
  // storage_units and stores so the single number reflects the whole bucket.
  byAssetGroupSoC: Map<string, number>
  // Per-(bus, category) effective TOTAL CAPACITY (MW) for the selected
  // snapshot's investment period. Includes parent.p_nom + Σ vintage.p_nom_opt
  // for every vintage with build_year ≤ snapshot.period. Used by the
  // asset-group label so the displayed "300 MW" → "593 MW" when a vintage
  // built in 2028 becomes active. Falls back to current p_nom_opt (the
  // aggregated horizon-end value) when vintage_results aren't available
  // or the snapshot index isn't multi-period.
  byAssetGroupCapacity: Map<string, number>
}

const EMPTY: OverlayData = {
  enabled: false, idx: 0, iso: '', kind: 'p',
  byBus: new Map(), byLine: new Map(), byLink: new Map(),
  byAssetGroup: new Map(), byAssetGroupSoC: new Map(),
  byAssetGroupCapacity: new Map(),
}

const CanvasResultsContext = createContext<OverlayData>(EMPTY)
export const useCanvasResults = () => useContext(CanvasResultsContext)

// Backend's _ts_payload returns `periods` as a separate int[] alongside
// `index` (string[]) for multi-period results — see routers/simulation.py
// _ts_payload. Single-period results have no `periods` key.
//
// `range` describes what was actually served (services/serialization.py::
// slice_ts) — present only on ranged responses. Absent means the payload is
// the whole series, the pre-range shape unconverted callers still receive.
interface TSRangeMeta {
  from: number
  to: number
  total: number
  complete: boolean
  capped: boolean
}
interface TSPayload {
  index: string[]
  columns: string[]
  data: number[][]
  periods?: number[]
  range?: TSRangeMeta
}

// Build a lookup for a single snapshot row of a TSPayload, mapping each column
// name to its numeric value. Returns null when the row index is out of range.
function rowMap(ts: TSPayload | null | undefined, row: number): Map<string, number> | null {
  if (!ts || row < 0 || row >= ts.data.length) return null
  const r = ts.data[row]
  const m = new Map<string, number>()
  ts.columns.forEach((c, i) => {
    const v = r[i]
    if (Number.isFinite(v)) m.set(c, v)
  })
  return m
}

// Mirrors api/simulation.ts's (unexported) ResultSource. Kept local rather
// than exporting a duplicate from there — this is the only consumer that
// needs it as a standalone type name.
type ResultSource = 'lopf' | 'ac_pf'

// A single-row probe: cheapest possible request that still returns the
// payload's `columns` (for width) and `range.total` (for horizon length).
const PROBE: TSRange = { from: 0, to: 0 }

/**
 * One result series, fetched as an aligned chunk around the scrubber.
 *
 * Two queries per series. The PROBE asks for a single row; its response
 * carries the full `columns` array and `range.total`, which is everything
 * needed to size the chunk. It costs a few KB, is cached forever
 * (`staleTime: Infinity`), and reuses the range parameter rather than
 * needing a metadata endpoint.
 *
 * The chunk MUST be derived per series: `line_reactive` is far narrower than
 * `generators`, and one shared size would size every request by whichever
 * series happened to be probed first.
 */
function useChunkedSeries(
  name: string,
  fetch: (source: ResultSource, range?: TSRange) => Promise<TSPayload | null>,
  opts: {
    project: string | null
    source: ResultSource
    idx: number
    enabled: boolean
  },
): TSPayload | null | undefined {
  const { project, source, idx, enabled } = opts

  const { data: probe } = useQuery({
    queryKey: nk(project, 'results', name, source, 'probe'),
    queryFn: () => fetch(source, PROBE),
    enabled,
    staleTime: Infinity,
  })

  const total = probe?.range?.total ?? 0
  const chunk = useMemo(
    () => chooseChunk(probe?.columns.length ?? 0, total),
    [probe?.columns.length, total],
  )
  const bounds = useMemo(
    // `chunkBounds`' third argument (`clampTo`) would stop a chunk crossing
    // an investment-period boundary — but the period selection
    // (SnapshotPicker's `activeRange`, derived from a component-local
    // `useState`) isn't shared store state today, so there is nothing here
    // to clamp against. Deliberately omitted, not forgotten: the canvas
    // reads exactly one row, offset by this payload's own `range.from` (see
    // `localIdx` below), so a chunk that happens to span a period boundary
    // just carries some rows the user isn't currently looking at — never
    // rendered, never mislabelled. Revisit once period selection moves into
    // shared state.
    () => chunkBounds(idx, chunk, total),
    [idx, chunk, total],
  )

  const { data } = useQuery({
    // `bounds.from` is what makes the cache work: identical for every index
    // inside a chunk, different across a boundary.
    queryKey: nk(project, 'results', name, source, bounds.from),
    queryFn: () => fetch(source, bounds),
    enabled: enabled && total > 0,
  })
  return data
}

export function CanvasResultsProvider({ children }: { children: ReactNode }) {
  const { resultsOverlayEnabled, resultsSnapshotIdx, resultSource, flowOverlayKind, currentProject } = useUIStore()

  // Fetch whenever the overlay is on. The provider is mounted by whichever
  // canvas is active (blank schematic OR satellite/hybrid map) — never both —
  // so there's no double-fetch, and the two canvases share the exact same
  // per-snapshot result aggregation.
  const enableQueries = resultsOverlayEnabled
  // Reactive flow is only fetched when the user is on the AC PF source AND
  // explicitly switches to the Q view. Otherwise we save the round-trip.
  const needReactive = enableQueries && resultSource === 'ac_pf' && flowOverlayKind === 'q'

  // Each series is fetched as an aligned chunk around the scrubber rather
  // than in full — see useChunkedSeries above. `common` is a fresh object
  // literal every render, which is fine: every field is a primitive or
  // already-memoised value, and useQuery keys off `queryKey`, not the
  // options object's identity.
  const common = {
    project: currentProject,
    source: resultSource,
    idx: resultsSnapshotIdx,
    enabled: enableQueries,
  }

  const gensTS  = useChunkedSeries('generators',       resultsApi.getGeneratorResults,       common)
  const loadTS  = useChunkedSeries('loads',             resultsApi.getLoadResults,            common)
  const linesTS = useChunkedSeries('lines',             resultsApi.getLineResults,            common)
  // Link active power (p0 at bus0, signed). Backend serves the same shape
  // as /results/lines — {index, columns: link_names[], data: number[][]}.
  // Needed for the playback overlay to animate H2 / heat / DC link flows
  // alongside the line flows the canvas already animates. Enabled on the
  // same gate; no extra round-trip when the overlay is off.
  const linksTS = useChunkedSeries('links',             resultsApi.getLinkResults,            common)
  // Storage / store dispatch — same shape as the other TS payloads
  // (index/columns/data). Sign: positive = discharge (injection), negative
  // = charge (consumption). State of charge is fetched separately below so
  // we can render an SoC % alongside the dispatch arrow for BESS cards.
  const storageDispatchTS = useChunkedSeries('storage_dispatch', resultsApi.getStorageDispatchResults, common)
  const storeDispatchTS   = useChunkedSeries('store_dispatch',   resultsApi.getStoreDispatchResults,   common)
  // Energy state for SoC %:
  //   • storage_units → state_of_charge (MWh) / (max_hours × p_nom)
  //   • stores        → e (MWh)              / e_nom
  // Aggregated together when a single (bus, Storage) group mixes both.
  const storageSoCTS  = useChunkedSeries('storage',      resultsApi.getStorageResults,     common)
  const storeEnergyTS = useChunkedSeries('store_energy', resultsApi.getStoreEnergyResults, common)
  // Reactive line flow — only fetched when actually needed (Q view + AC PF).
  // Keyed separately so flipping the Q toggle on doesn't invalidate the P
  // query (the P series stays valid for loading-% colouring). `needReactive`
  // is only true when resultSource is already 'ac_pf', so `common.source`
  // is correct here without an override.
  const linesReactiveTS = useChunkedSeries(
    'line_reactive', resultsApi.getLineReactive, { ...common, enabled: needReactive },
  )

  const { data: generators = [] } = useQuery({
    queryKey: nk(currentProject, 'generators'), queryFn: networkApi.getGenerators,
    enabled: enableQueries,
  })
  const { data: loads = [] } = useQuery({
    queryKey: nk(currentProject, 'loads'), queryFn: networkApi.getLoads,
    enabled: enableQueries,
  })
  const { data: lines = [] } = useQuery({
    queryKey: nk(currentProject, 'lines'), queryFn: networkApi.getLines,
    enabled: enableQueries,
  })
  // Link metadata for per-link aggregation. Used to look up bus0 / bus1 /
  // carrier / p_nom when joining against linksTS for the overlay.
  const { data: links = [] } = useQuery({
    queryKey: nk(currentProject, 'links'), queryFn: networkApi.getLinks,
    enabled: enableQueries,
  })
  const { data: storageUnits = [] } = useQuery({
    queryKey: nk(currentProject, 'storage_units'), queryFn: networkApi.getStorageUnits,
    enabled: enableQueries,
  })
  const { data: stores = [] } = useQuery({
    queryKey: nk(currentProject, 'stores'), queryFn: networkApi.getStores,
    enabled: enableQueries,
  })

  // Per-vintage build results. Lets the asset-group label show the
  // CORRECT effective capacity for the selected snapshot's period —
  // e.g. a Solar2@2028 vintage with p_nom_opt=293 only contributes to
  // Solar2's effective MW when the snapshot's period is ≥ 2028. Without
  // this lookup the label always shows the horizon-end aggregated p_nom
  // (593 MW), which is misleading when scrubbing through 2026 hourlies.
  const { data: vintageResultsRaw } = useQuery({
    queryKey: nk(currentProject, 'vintage_results'),
    queryFn: () => networkApi.listVintageResults(),
    enabled: enableQueries,
  })

  const value = useMemo<OverlayData>(() => {
    const ts = (gensTS as TSPayload | null) ?? null
    const lts = (loadTS as TSPayload | null) ?? null
    const lints = (linesTS as TSPayload | null) ?? null
    const lkts = (linksTS as TSPayload | null) ?? null
    if (!enableQueries || (!ts && !lts && !lints && !lkts)) return EMPTY

    // `range.total` is the HORIZON — the series' true snapshot count —
    // while `data.length` is only the chunk that happened to be fetched.
    // Clamping against the chunk (the pre-chunking behaviour) would
    // silently render the WRONG snapshot's flows: asking for snapshot 5000
    // would clamp to a chunk length of e.g. 167 and render row 167's flows
    // labelled as snapshot 5000's. Fall back to `data.length` only for the
    // (pre-range) unconverted shape, where there is no `range` block at
    // all. Pull from whichever TS payload arrived first — handles
    // link-only networks (no Lines or Generators) gracefully.
    const horizon = ts?.range?.total ?? lts?.range?.total ?? lints?.range?.total ?? lkts?.range?.total
      ?? ts?.data.length ?? lts?.data.length ?? lints?.data.length ?? lkts?.data.length ?? 0
    if (horizon === 0) return EMPTY
    const idx = Math.max(0, Math.min(resultsSnapshotIdx, horizon - 1))

    // Each payload is probed and chunked independently (see
    // useChunkedSeries), so two payloads can sit on different chunks at the
    // same snapshot index — the global→local offset must be computed PER
    // PAYLOAD, not once. `rowMap` already returns null for a chunk-local
    // index outside that payload's rows, which renders nothing for it — the
    // safe failure — rather than a neighbouring row's numbers under this
    // snapshot's label.
    const localIdx = (p: TSPayload | null) => idx - (p?.range?.from ?? 0)
    const iso = ts?.index[localIdx(ts)] ?? lts?.index[localIdx(lts)]
      ?? lints?.index[localIdx(lints)] ?? lkts?.index[localIdx(lkts)] ?? ''

    const gMap = rowMap(ts, localIdx(ts))
    const lMap = rowMap(lts, localIdx(lts))
    const linMap = rowMap(lints, localIdx(lints))

    // Per-bus aggregation: walk every generator / load, sum power into its
    // bus AND into the per-carrier sub-bucket so the new stacked-donut
    // bus indicator on TopologyCanvas can render each carrier's slice
    // without re-aggregating in the consumer.
    //
    // Carrier-naming convention: pulled raw from the component's own
    // `.carrier` field, blank → `'unspecified'`. Consumers wanting alias
    // normalisation (battery / BESS / Li-Ion → 'battery') should pipe
    // through carrierAliases on read; the context stays carrier-agnostic
    // so it doesn't lose information.
    type BusEntry = { gen: number; load: number; byCarrier: Map<string, { gen: number; load: number }> }
    const byBus = new Map<string, BusEntry>()
    const getBusEntry = (bus: string): BusEntry => {
      const existing = byBus.get(bus)
      if (existing) return existing
      const fresh: BusEntry = { gen: 0, load: 0, byCarrier: new Map() }
      byBus.set(bus, fresh)
      return fresh
    }
    const bumpCarrier = (entry: BusEntry, carrier: string, dGen: number, dLoad: number) => {
      const key = (carrier ?? '').trim() || 'unspecified'
      const cur = entry.byCarrier.get(key) ?? { gen: 0, load: 0 }
      cur.gen  += dGen
      cur.load += dLoad
      entry.byCarrier.set(key, cur)
    }
    if (gMap) {
      for (const g of generators as Generator[]) {
        const v = gMap.get(g.name)
        if (v == null) continue
        const cur = getBusEntry(g.bus)
        cur.gen += v
        bumpCarrier(cur, g.carrier ?? '', v, 0)
      }
    }
    if (lMap) {
      for (const ld of loads as Load[]) {
        const v = lMap.get(ld.name)
        if (v == null) continue
        const cur = getBusEntry(ld.bus)
        cur.load += v
        bumpCarrier(cur, ld.carrier ?? '', 0, v)
      }
    }

    // Per-asset-group aggregation, mirroring TopologyCanvas's
    // buildAssetDescriptors split: Thermal / Renewables / Storage / Load.
    // Key = `${busName}|${category}` so AssetGroupNode can look up its own
    // value with just its data.busName + data.category — no need to pass the
    // asset name list around.
    const sdMap = rowMap(storageDispatchTS as TSPayload | null, localIdx(storageDispatchTS as TSPayload | null))
    const stMap = rowMap(storeDispatchTS as TSPayload | null, localIdx(storeDispatchTS as TSPayload | null))
    const byAssetGroup = new Map<string, number>()
    const bumpGroup = (busName: string, category: string, v: number) => {
      const k = `${busName}|${category}`
      byAssetGroup.set(k, (byAssetGroup.get(k) ?? 0) + v)
    }
    if (gMap) {
      for (const g of generators as Generator[]) {
        const v = gMap.get(g.name)
        if (v == null) continue
        bumpGroup(g.bus, isRenewableCarrier(g.carrier) ? 'Renewables' : 'Thermal', v)
      }
    }
    if (lMap) {
      for (const ld of loads as Load[]) {
        const v = lMap.get(ld.name)
        if (v == null) continue
        bumpGroup(ld.bus, 'Load', v)
      }
    }
    if (sdMap) {
      for (const s of storageUnits as StorageUnit[]) {
        const v = sdMap.get(s.name)
        if (v == null) continue
        bumpGroup(s.bus, 'Storage', v)
      }
    }
    if (stMap) {
      for (const s of stores as Store[]) {
        const v = stMap.get(s.name)
        if (v == null) continue
        bumpGroup(s.bus, 'Storage', v)
      }
    }

    // ── SoC % per Storage group ─────────────────────────────────────────
    // Σenergy / Σcapacity at this snapshot, expressed as a percentage.
    // Numerator: state_of_charge (MWh) for storage_units + e (MWh) for stores.
    // Denominator: max_hours × p_nom_opt (or p_nom) for storage_units +
    //              e_nom_opt (or e_nom) for stores. Use *_opt when present
    //              so post-solve expansion sizes the capacity correctly.
    const socMap = rowMap(storageSoCTS as TSPayload | null, localIdx(storageSoCTS as TSPayload | null))
    const eMap   = rowMap(storeEnergyTS as TSPayload | null, localIdx(storeEnergyTS as TSPayload | null))
    const socAcc = new Map<string, { energy: number; capacity: number }>()
    const bumpSoC = (busName: string, energy: number, capacity: number) => {
      if (!Number.isFinite(energy) || !Number.isFinite(capacity) || capacity <= 0) return
      const cur = socAcc.get(busName) ?? { energy: 0, capacity: 0 }
      cur.energy += energy
      cur.capacity += capacity
      socAcc.set(busName, cur)
    }
    if (socMap) {
      for (const s of storageUnits as StorageUnit[]) {
        const soc = socMap.get(s.name)
        if (soc == null) continue
        // p_nom_opt is set after solve when extendable; fall back to p_nom.
        const pNom = ((s as unknown as { p_nom_opt?: number }).p_nom_opt
          ?? s.p_nom ?? 0)
        const cap = (s.max_hours ?? 0) * pNom
        bumpSoC(s.bus, soc, cap)
      }
    }
    if (eMap) {
      for (const s of stores as Store[]) {
        const e = eMap.get(s.name)
        if (e == null) continue
        const cap = ((s as unknown as { e_nom_opt?: number }).e_nom_opt
          ?? s.e_nom ?? 0)
        bumpSoC(s.bus, e, cap)
      }
    }
    const byAssetGroupSoC = new Map<string, number>()
    for (const [bus, { energy, capacity }] of socAcc) {
      byAssetGroupSoC.set(`${bus}|Storage`, (energy / capacity) * 100)
    }

    // Per-line: |p0| + loading% relative to s_nom_opt (post-solve) or s_nom.
    // q0 from the reactive series, when loaded — `null` otherwise so the
    // consumer can hide the Q value in the edge tooltip.
    const qMap = rowMap(linesReactiveTS as TSPayload | null, localIdx(linesReactiveTS as TSPayload | null))
    const byLine = new Map<string, { p0: number; q0: number | null; loadingPct: number; sNom: number }>()
    if (linMap) {
      for (const ln of lines as LineT[]) {
        const p0 = linMap.get(ln.name)
        if (p0 == null) continue
        const q0 = qMap ? (qMap.get(ln.name) ?? null) : null
        const sNomOpt = (ln as unknown as { s_nom_opt?: number }).s_nom_opt
        const sNom = (sNomOpt && Number.isFinite(sNomOpt) && sNomOpt > 0)
          ? sNomOpt
          : (ln.s_nom ?? 0)
        const loadingPct = sNom > 0 ? Math.abs(p0) / sNom * 100 : 0
        byLine.set(ln.name, { p0, q0, loadingPct, sNom })
      }
    }

    // Per-link: same shape as byLine but for Link components. Animates H2
    // / heat / DC flows that the canvas already draws statically. The
    // backend's `/results/links` payload uses link names as columns and
    // p0 (bus0-side active power) as the per-row value — same convention
    // the Dispatch tab consumes for its DispatchStack. Multi-port links
    // (bus2 / bus3, e.g. waste-heat coupled heat pumps) still produce a
    // p0 entry; the secondary ports are exposed in /results/links_p1 etc.
    // (not consumed here — V1 covers the dominant flow direction only).
    const linkMap = rowMap(linksTS as TSPayload | null, localIdx(linksTS as TSPayload | null))
    const byLink = new Map<string, {
      p0: number; loadingPct: number; pNom: number
      carrier: string; bus0: string; bus1: string
    }>()
    if (linkMap) {
      for (const lk of links as LinkT[]) {
        const p0 = linkMap.get(lk.name)
        if (p0 == null) continue
        const pNomOpt = (lk as unknown as { p_nom_opt?: number }).p_nom_opt
        const pNom = (pNomOpt && Number.isFinite(pNomOpt) && pNomOpt > 0)
          ? pNomOpt
          : (lk.p_nom ?? 0)
        const loadingPct = pNom > 0 ? Math.abs(p0) / pNom * 100 : 0
        byLink.set(lk.name, {
          p0, loadingPct, pNom,
          carrier: lk.carrier ?? '',
          bus0: lk.bus0, bus1: lk.bus1,
        })
      }
    }

    // ── Per-(bus, category) effective capacity at this snapshot's period ──
    // Walks vintage_results to compute the effective p_nom per asset for the
    // snapshot's investment period. Falls back to the asset's static p_nom
    // (or p_nom_opt when no vintage results exist) so single-period and
    // non-extendable assets still report the right number.
    //
    // Period for THIS snapshot — read directly from the multi-period
    // payload's `periods` array (mirrors the MultiIndex's level-0). For
    // flat / single-period payloads `periods` is undefined → null
    // (effectiveCapForAsset then sums every vintage as if all are active).
    // Include `lkts` in the fallback chain so a link-only network (no
    // generator / load / line dispatch but plenty of link flows — H2-only
    // sub-networks for instance) still resolves its investment-period axis.
    // QA-flagged at V1/V2 review.
    const tsAny = (ts ?? lts ?? lints ?? lkts) as TSPayload | null
    // `tsAny.periods` is parallel to `tsAny.index`/`tsAny.data` — chunk-local
    // like everything else on this payload — so it needs the same
    // global→local offset as `gMap`/`lMap`/etc., not the raw global `idx`.
    const tsAnyLocalIdx = localIdx(tsAny)
    const periodFromIso: number | null = (
      tsAny?.periods && tsAnyLocalIdx >= 0 && tsAnyLocalIdx < tsAny.periods.length
        ? tsAny.periods[tsAnyLocalIdx]
        : null
    )
    const vrAll = (vintageResultsRaw?.results ?? {}) as Record<string, Record<string, {
      initial_capacity: number
      periods: Array<{ build_year: number; p_nom_opt: number }>
    }>>
    const effectiveCapForAsset = (cls: string, name: string, fallback: number): number => {
      const entry = vrAll?.[cls]?.[name]
      if (!entry) return fallback
      const initial = entry.initial_capacity ?? fallback
      if (periodFromIso == null) {
        // Single-period or flat — sum every vintage on top of initial.
        return (entry.periods ?? []).reduce(
          (s, p) => s + (p.p_nom_opt ?? 0), initial,
        )
      }
      let total = initial
      for (const p of entry.periods ?? []) {
        if (p.build_year != null && p.build_year <= periodFromIso) {
          total += p.p_nom_opt ?? 0
        }
      }
      return total
    }

    const byAssetGroupCapacity = new Map<string, number>()
    const bumpCap = (busName: string, category: string, mw: number) => {
      const k = `${busName}|${category}`
      byAssetGroupCapacity.set(k, (byAssetGroupCapacity.get(k) ?? 0) + mw)
    }
    for (const g of generators as Generator[]) {
      const fallback = ((g as unknown as { p_nom_opt?: number }).p_nom_opt
        ?? g.p_nom ?? 0)
      const cap = effectiveCapForAsset('Generator', g.name, fallback)
      bumpCap(g.bus, isRenewableCarrier(g.carrier) ? 'Renewables' : 'Thermal', cap)
    }
    for (const s of storageUnits as StorageUnit[]) {
      const fallback = ((s as unknown as { p_nom_opt?: number }).p_nom_opt
        ?? s.p_nom ?? 0)
      const cap = effectiveCapForAsset('StorageUnit', s.name, fallback)
      bumpCap(s.bus, 'Storage', cap)
    }
    for (const s of stores as Store[]) {
      const fallback = ((s as unknown as { e_nom_opt?: number }).e_nom_opt
        ?? s.e_nom ?? 0)
      const cap = effectiveCapForAsset('Store', s.name, fallback)
      bumpCap(s.bus, 'Storage', cap)
    }
    // Loads have no capacity expansion — skip.

    return {
      enabled: true,
      idx, iso,
      kind: flowOverlayKind,
      byBus, byLine, byLink, byAssetGroup, byAssetGroupSoC,
      byAssetGroupCapacity,
    }
  }, [
    enableQueries, resultsSnapshotIdx, gensTS, loadTS, linesTS, linksTS, linesReactiveTS,
    storageDispatchTS, storeDispatchTS, storageSoCTS, storeEnergyTS,
    generators, loads, lines, links, storageUnits, stores, flowOverlayKind,
    vintageResultsRaw,
  ])

  return (
    <CanvasResultsContext.Provider value={value}>
      {children}
    </CanvasResultsContext.Provider>
  )
}

// Helper for consumers — pretty-print MW in either MW or kW depending on size.
export function fmtMW(mw: number, digits = 1): string {
  const a = Math.abs(mw)
  if (a < 1) return `${(mw * 1000).toFixed(0)} kW`
  if (a >= 1000) return `${(mw / 1000).toFixed(digits)} GW`
  return `${mw.toFixed(digits)} MW`
}

// Color band for line loading: green/amber/red.
export function loadingColor(pct: number): string {
  if (pct >= 90) return '#dc2626'
  if (pct >= 50) return '#d97706'
  return '#16a34a'
}
