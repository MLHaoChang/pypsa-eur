import { useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip as RTooltip, Legend,
} from 'recharts'
import { Download } from 'lucide-react'
import toast from 'react-hot-toast'
import { resultsApi } from '../../api/simulation'
import { networkApi } from '../../api/network'
import type { Bus, Line as LineT, Transformer } from '../../api/types'
import { type TSPayload, fmtPower, shortStamp, downloadCSV, KPI, ChartActions, Seg,
  CHART_GRID, CHART_AXIS, CHART_LEGEND, CHART_TOOLTIP, yAxisLabel,
  useSeasonalViewMode, SeasonalLineCardGrid,
  WEEKLY_MIN_DAYS, MONTHLY_MIN_DAYS } from './shared'
import { useResultsFilter, resolveRange } from './filterContext'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'

// ── Load Flow tab ─────────────────────────────────────────────────────────────
// Three sections:
//   1. KPI strip — peak network flow, mean loading %, mean marginal price
//   2. Line table — peak/mean |p0|, loading %, s_nom (CSV-exportable)
//   3. Top-loaded lines time-series chart (top 5 by mean loading)
//   4. Bus table — mean marginal price, voltage angle (when available)

const TOP_N_CHART = 5

// CO₂ emissions, the hourly price chart, and the price-drivers table were
// moved to the dedicated Emissions / Prices tabs but their JSX is kept here
// (disabled) pending a follow-up cleanup. Typed `boolean` — not the literal
// `false` — on purpose: a literal-`false` guard makes TS treat the branch as
// dead code and skip control-flow narrowing, which surfaces dozens of
// spurious "possibly null" errors on the result variables inside.
const SHOW_RETIRED_SECTIONS = false as boolean

export default function LoadFlow() {
  // Refs for SVG export — one per chart section.
  const voltageChartRef = useRef<HTMLDivElement | null>(null)
  const topLinesChartRef = useRef<HTMLDivElement | null>(null)
  const topTrafoChartRef = useRef<HTMLDivElement | null>(null)
  // resultSource is in the queryKey so every result query re-fetches when the
  // user flips the AC PF toggle (P2). Backend honours the param via ?source=.
  const { resultSource, setResultSource } = useUIStore()
  const currentProject = useUIStore(s => s.currentProject)
  const { data: acPfStatus } = useQuery({ queryKey: nk(currentProject, 'results', 'ac_pf', 'status'), queryFn: resultsApi.getAcPfStatus })
  const acPfAvailable = !!acPfStatus?.available

  const { data: linesTS } = useQuery({ queryKey: nk(currentProject, 'results', 'lines', resultSource),  queryFn: () => resultsApi.getLineResults(resultSource) })
  // Transformer flows — same shape as lines (per-snapshot p0 on bus0 side).
  // Drives the new Transformers loading table + chart below.
  const { data: trafosTS } = useQuery({ queryKey: nk(currentProject, 'results', 'transformers', resultSource), queryFn: () => resultsApi.getTransformerResults(resultSource) })
  // Marginal prices are LP duals — they only exist on the LOPF/SCLOPF solve.
  // PyPSA's `n.pf()` doesn't compute duals, so `?source=ac_pf` would return
  // zeros. Pin to 'lopf' regardless of the active view toggle so the price
  // chart and table stay meaningful even when the user is inspecting AC PF
  // results elsewhere on the page.
  const { data: priceTS } = useQuery({ queryKey: nk(currentProject, 'results', 'prices', 'lopf'), queryFn: () => resultsApi.getPrices('lopf') })
  // CO₂ emissions per-carrier + per-generator, plus shadow price of any
  // primary-energy CO₂ cap. Backend reads dispatched energy × carrier
  // intensity / efficiency. Same source as LOPF dispatch (LP stage).
  const { data: emissions } = useQuery({ queryKey: nk(currentProject, 'results', 'emissions'), queryFn: resultsApi.getEmissions })
  // Per-carrier KPIs (CF / curtailment / market value / revenue). Wraps
  // PyPSA's n.statistics helpers.
  const { data: carrierKpis } = useQuery({ queryKey: nk(currentProject, 'results', 'carrier_kpis'), queryFn: resultsApi.getCarrierKpis })
  // Line congestion shadow prices — LP duals on the s_nom capacity constraints.
  // Surfaces which corridors bind and the marginal value of additional capacity.
  const { data: lineDuals } = useQuery({ queryKey: nk(currentProject, 'results', 'line_duals'), queryFn: resultsApi.getLineDuals })
  // Unit-commitment results — only populated when committable=True on at
  // least one generator (MILP path). Returns the binary status grid for the
  // heatmap + per-generator UC stats (starts, shuts, on-hours, UC costs).
  const { data: ucResults } = useQuery({ queryKey: nk(currentProject, 'results', 'unit_commitment'), queryFn: resultsApi.getUnitCommitment })
  // Transmission losses — single source of truth for both the primary
  // panel and the LP-vs-AC-PF comparison block. Previously we issued three
  // queries: one keyed by `resultSource` + two `__compare/{lopf,ac_pf}`.
  // When `acPfAvailable && resultSource === 'lopf'`, the resultSource
  // query and the `__compare/lopf` query were redundant identical work
  // dedupe-keyed differently — wasted round-trip per LoadFlow mount.
  // Use the two source-specific queries (gated on `acPfAvailable`) as the
  // primary, and derive `lossesData` from whichever matches the current
  // resultSource. Falls back to the resultSource-keyed query only when
  // AC PF isn't available (single LOPF call, no duplicate).
  const { data: lossesLopf } = useQuery({
    queryKey: nk(currentProject, 'results', 'losses', 'lopf'),
    queryFn: () => resultsApi.getLosses('lopf'),
  })
  const { data: lossesAcPf } = useQuery({
    queryKey: nk(currentProject, 'results', 'losses', 'ac_pf'),
    queryFn: () => resultsApi.getLosses('ac_pf'),
    enabled: acPfAvailable,
  })
  const lossesData = resultSource === 'ac_pf' ? lossesAcPf : lossesLopf
  // AC-PF-only result series. Only fetched when Stage 2 is available — keeps
  // the network round-trip empty for plain-LOPF runs where these endpoints
  // would return 204. Each returns a TSPayload (snapshot × asset) or null.
  const { data: voltagesTS } = useQuery({
    queryKey: nk(currentProject, 'results', 'voltages', 'ac_pf'),
    queryFn: () => resultsApi.getVoltages('ac_pf'),
    enabled: acPfAvailable,
  })
  const { data: lineReactiveTS } = useQuery({
    queryKey: nk(currentProject, 'results', 'line_reactive', 'ac_pf'),
    queryFn: () => resultsApi.getLineReactive('ac_pf'),
    enabled: acPfAvailable,
  })
  const { data: trafoReactiveTS } = useQuery({
    queryKey: nk(currentProject, 'results', 'transformer_reactive', 'ac_pf'),
    queryFn: () => resultsApi.getTransformerReactive('ac_pf'),
    enabled: acPfAvailable,
  })

  // Snapshots payload — used to translate convergence-map keys into clickable
  // entries that update the snapshot picker on the canvas.
  const { data: snap } = useQuery({ queryKey: nk(currentProject, 'snapshots'), queryFn: networkApi.getSnapshots })
  const setResultsSnapshotIdx = useUIStore(s => s.setResultsSnapshotIdx)
  // The prices endpoint returns TWO parallel data grids: the raw LP duals
  // (`data`) and a merit-order view (`data_adjusted`) which adds back the
  // curtailment-cost subsidy that the solver's extra_functionality term
  // injects on renewables. Toggle below picks which series the chart
  // consumes — negative-price hours visible in `data` typically disappear
  // in `data_adjusted` because they were artefacts of that subsidy.
  type PricesPayload = TSPayload & {
    data_adjusted?: number[][]
    negative_hours?: number
  }

  const { data: lines = [] } = useQuery({ queryKey: nk(currentProject, 'lines'), queryFn: networkApi.getLines })
  const { data: transformers = [] } = useQuery({ queryKey: nk(currentProject, 'transformers'), queryFn: networkApi.getTransformers })
  const { data: buses = [] } = useQuery({ queryKey: nk(currentProject, 'buses'), queryFn: networkApi.getBuses })

  const tsLines = linesTS as TSPayload | null
  const tsTrafos = trafosTS as TSPayload | null

  // Period filter — narrows time-series-driven stats (peak / mean / loading)
  // to the selected investment period. Aggregated (selectedPeriod=null) scans
  // every row; per-period limits to a contiguous slice. Iso clamps still
  // apply on top (today's UI only exposes period switching, not the iso
  // bounds, in LoadFlow — but resolveRange would handle both if added later).
  const filter = useResultsFilter()
  const refIndex = tsLines?.index ?? tsTrafos?.index ?? []
  const refPeriods = tsLines?.periods ?? tsTrafos?.periods
  const range = resolveRange(refIndex, filter, refPeriods)

  // Seasonal averaged view applies to all three time-series charts on this
  // tab (voltage, top-loaded lines, top-loaded transformers). One Seg at the
  // top controls all three for visual consistency with Dispatch / Prices.
  const {
    setViewMode: setLoadFlowViewMode, effectiveMode: lfMode,
    weeklyAvailable: lfWeeklyOk, monthlyAvailable: lfMonthlyOk,
  } = useSeasonalViewMode('results:loadflow-viewmode', refIndex, range)

  // Per-transformer stats: same shape as line stats so the table renders the
  // same way. Helper computes peak/mean/loading from a (snapshot × asset)
  // time series. Kept inline rather than abstracted into a util because the
  // line and transformer flows have different field names (s_nom_opt vs the
  // same on transformers) that are easy to mix up in a generic helper.
  function computeStats<T extends { name: string; s_nom: number }>(
    ts: TSPayload | null, assets: T[]
  ) {
    if (!ts) return null
    const byName = new Map(assets.map(a => [a.name, a]))
    const rFrom = Math.max(0, range.from)
    const rTo = Math.min(ts.data.length - 1, range.to)
    return ts.columns.map((name, ci) => {
      let peak = 0, sumAbs = 0, n = 0
      if (rFrom <= rTo) {
        for (let row = rFrom; row <= rTo; row++) {
          const v = ts.data[row][ci]
          if (Number.isFinite(v)) {
            const a = Math.abs(v)
            if (a > peak) peak = a
            sumAbs += a; n += 1
          }
        }
      }
      const mean = n ? sumAbs / n : 0
      const a = byName.get(name)
      const sNom = a?.s_nom ?? 0
      const sNomOpt = (a as unknown as { s_nom_opt?: number } | undefined)?.s_nom_opt ?? sNom
      const sCap = sNomOpt > 0 ? sNomOpt : sNom
      const loadingPct = sCap > 0 ? (peak / sCap) * 100 : 0
      return {
        name,
        bus0: (a as unknown as { bus0?: string } | undefined)?.bus0 ?? '',
        bus1: (a as unknown as { bus1?: string } | undefined)?.bus1 ?? '',
        s_nom: sNom,
        s_nom_opt: sNomOpt,
        peak, mean, loadingPct,
      }
    }).sort((a, b) => b.loadingPct - a.loadingPct)
  }

  // Per-line stats: peak |p0|, mean |p0|, s_nom, loading% — scoped to the
  // active period via `range`.
  const lineStats = useMemo(() => {
    if (!tsLines) return null
    const lineByName = new Map((lines as LineT[]).map(l => [l.name, l]))
    const rFrom = Math.max(0, range.from)
    const rTo = Math.min(tsLines.data.length - 1, range.to)
    return tsLines.columns.map((name, ci) => {
      let peak = 0, sumAbs = 0, n = 0
      if (rFrom <= rTo) {
        for (let row = rFrom; row <= rTo; row++) {
          const v = tsLines.data[row][ci]
          if (Number.isFinite(v)) {
            const a = Math.abs(v)
            if (a > peak) peak = a
            sumAbs += a; n += 1
          }
        }
      }
      const mean = n ? sumAbs / n : 0
      const ln = lineByName.get(name)
      const sNom = ln?.s_nom ?? 0
      const sNomOpt = (ln as unknown as { s_nom_opt?: number } | undefined)?.s_nom_opt ?? sNom
      const sCap = sNomOpt > 0 ? sNomOpt : sNom
      const loadingPct = sCap > 0 ? (peak / sCap) * 100 : 0
      return {
        name,
        bus0: ln?.bus0 ?? '',
        bus1: ln?.bus1 ?? '',
        s_nom: sNom,
        s_nom_opt: sNomOpt,
        peak, mean, loadingPct,
      }
    }).sort((a, b) => b.loadingPct - a.loadingPct)
  }, [tsLines, lines, range.from, range.to])

  // Per-transformer stats (same shape as lineStats — see helper above).
  const trafoStats = useMemo(
    () => computeStats(tsTrafos, transformers as Transformer[]),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [tsTrafos, transformers, range.from, range.to],
  )

  // Top-N most loaded lines as a per-snapshot multi-line chart. Recharts wants
  // {snapshot, lineA, lineB, ...} rows; we slice to the top-N by mean loading.
  // Rows are scoped to `range` so per-period view only shows that period's
  // hours — single-period or "Aggregated" sees the full series.
  const topChart = useMemo(() => {
    if (!tsLines || !lineStats) return null
    const top = lineStats.slice(0, TOP_N_CHART)
    const colIdxByName = new Map(tsLines.columns.map((c, i) => [c, i]))
    const cols = top.map(t => t.name)
    const rFrom = Math.max(0, range.from)
    const rTo = Math.min(tsLines.data.length - 1, range.to)
    const rows: Array<Record<string, number | string | null>> = []
    for (let row = rFrom; row <= rTo; row++) {
      const stamp = tsLines.index[row]
      // Missing values are passed as `null` so Recharts skips them and the
      // line shows a gap. Coercing to 0 (the old behaviour) made AC-PF
      // non-convergent snapshots look like a fault — see audit P1.
      const obj: Record<string, number | string | null> = { snapshot: stamp }
      for (const c of cols) {
        const i = colIdxByName.get(c)
        const v = i != null ? tsLines.data[row][i] : null
        obj[c] = Number.isFinite(v as number) ? (v as number) : null
      }
      rows.push(obj)
    }
    return { rows, cols }
  }, [tsLines, lineStats, range.from, range.to])

  // Top-N most loaded transformers (analogous to topChart). Built lazily —
  // returns null when no transformer time series, so the section can opt out.
  const topTrafoChart = useMemo(() => {
    if (!tsTrafos || !trafoStats) return null
    const top = trafoStats.slice(0, TOP_N_CHART)
    const colIdxByName = new Map(tsTrafos.columns.map((c, i) => [c, i]))
    const cols = top.map(t => t.name)
    // Period-scoped slice — mirrors lineChart above.
    const rFrom = Math.max(0, range.from)
    const rTo = Math.min(tsTrafos.data.length - 1, range.to)
    const rows: Array<Record<string, number | string | null>> = []
    for (let row = rFrom; row <= rTo; row++) {
      const stamp = tsTrafos.index[row]
      const obj: Record<string, number | string | null> = { snapshot: stamp }
      for (const c of cols) {
        const i = colIdxByName.get(c)
        const v = i != null ? tsTrafos.data[row][i] : null
        obj[c] = Number.isFinite(v as number) ? (v as number) : null
      }
      rows.push(obj)
    }
    return { rows, cols }
  }, [tsTrafos, trafoStats, range.from, range.to])

  // Per-asset min/max/avg over a (snapshot × asset) time series.
  // Used by the AC-PF reactive-power tables and voltage tables.
  //
  // Range-aware: iterates only the slice `[range.from, range.to]` of
  // `ts.data` so KPI strips and tables respect the horizon-filter the
  // user picked. Previously this walked the full series unconditionally
  // — the charts (which slice via `range`) showed period-scoped data
  // but the per-asset tables stayed horizon-wide, producing two
  // disagreeing views of the same data in the same panel.
  function computeMinMaxAvg(
    ts: TSPayload | null,
    rng: { from: number; to: number },
  ) {
    if (!ts) return null
    const lastRow = ts.data.length - 1
    const rFrom = Math.max(0, Math.min(rng.from, lastRow))
    const rTo   = Math.max(0, Math.min(rng.to,   lastRow))
    return ts.columns.map((name, ci) => {
      let s = 0, n = 0, min = Infinity, max = -Infinity
      for (let row = rFrom; row <= rTo; row++) {
        const v = ts.data[row][ci]
        if (Number.isFinite(v)) {
          s += v
          n += 1
          if (v < min) min = v
          if (v > max) max = v
        }
      }
      return {
        name,
        min: n ? min : 0,
        max: n ? max : 0,
        mean: n ? s / n : 0,
      }
    })
  }

  // AC-PF reactive-power stats per line / transformer.
  const lineReactiveStats = useMemo(
    () => computeMinMaxAvg(lineReactiveTS as TSPayload | null, range),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [lineReactiveTS, range.from, range.to],
  )
  const trafoReactiveStats = useMemo(
    () => computeMinMaxAvg(trafoReactiveTS as TSPayload | null, range),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [trafoReactiveTS, range.from, range.to],
  )
  // Bus voltage stats (p.u.) — only populated after AC PF runs. The LP stage
  // leaves v_mag_pu at its default (1.0) so showing it for source='lopf' would
  // be misleading; the parent query is `enabled: acPfAvailable` so this is
  // automatically gated.
  const busVoltageStats = useMemo(() => {
    const stats = computeMinMaxAvg(voltagesTS as TSPayload | null, range)
    if (!stats) return null
    const byName = new Map((buses as Bus[]).map(b => [b.name, b]))
    return stats.map(s => ({
      ...s,
      v_nom: byName.get(s.name)?.v_nom ?? 0,
      carrier: byName.get(s.name)?.carrier ?? '',
    }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [voltagesTS, buses, range.from, range.to])

  // Per-snapshot per-bus voltage rows for the chart. Same shape as topChart
  // (Recharts wants {snapshot, busA, busB, ...} rows). Caps at 8 buses to keep
  // the legend readable; widest-deviation buses surface first.
  const voltageChart = useMemo(() => {
    const tsV = voltagesTS as TSPayload | null
    if (!tsV || !busVoltageStats) return null
    // Sort buses by largest deviation from 1.0 (|max-1| + |1-min|) so the most
    // interesting voltages render first.
    const ranked = [...busVoltageStats].sort((a, b) => {
      const da = Math.abs(a.max - 1) + Math.abs(1 - a.min)
      const db = Math.abs(b.max - 1) + Math.abs(1 - b.min)
      return db - da
    })
    const VOLTAGE_CHART_CAP = 8
    const cols = ranked.slice(0, VOLTAGE_CHART_CAP).map(b => b.name)
    const colIdx = new Map(tsV.columns.map((c, i) => [c, i]))
    const rFrom = Math.max(0, range.from)
    const rTo = Math.min(tsV.data.length - 1, range.to)
    const rows: Array<Record<string, number | string | null>> = []
    for (let row = rFrom; row <= rTo; row++) {
      const stamp = tsV.index[row]
      const obj: Record<string, number | string | null> = { snapshot: stamp }
      for (const c of cols) {
        const i = colIdx.get(c)
        const v = i != null ? tsV.data[row][i] : null
        // null (not 1.0!) for missing — a non-converged snapshot displaying
        // as exactly 1.0 p.u. would look like a perfectly-flat voltage,
        // masking the actual divergence.
        obj[c] = Number.isFinite(v as number) ? (v as number) : null
      }
      rows.push(obj)
    }
    return { cols, rows, busTotal: tsV.columns.length }
  }, [voltagesTS, busVoltageStats, range.from, range.to])

  // Per-bus stats: mean marginal price (LOPF), voltage angle (PF — populated
  // by n.pf() into n.buses_t.v_ang and v_mag_pu, neither currently exposed by
  // a /results endpoint, so we report only what we have).
  const busStats = useMemo(() => {
    const tsP = priceTS as TSPayload | null
    if (!tsP) return null
    const byName = new Map((buses as Bus[]).map(b => [b.name, b]))
    // Range-aware: respect the horizon-filter the user picked. Without
    // this slice the table + KPI strip showed horizon-wide stats while
    // the chart below showed period-scoped data — two disagreeing views
    // of the same data in the same panel.
    const lastRow = tsP.data.length - 1
    const rFrom = Math.max(0, Math.min(range.from, lastRow))
    const rTo   = Math.max(0, Math.min(range.to,   lastRow))
    return tsP.columns.map((name, ci) => {
      let s = 0, n = 0, peak = -Infinity, trough = Infinity
      for (let row = rFrom; row <= rTo; row++) {
        const v = tsP.data[row][ci]
        if (Number.isFinite(v)) {
          s += v; n += 1
          if (v > peak) peak = v
          if (v < trough) trough = v
        }
      }
      const mean = n ? s / n : 0
      return {
        name,
        carrier: byName.get(name)?.carrier ?? '',
        v_nom:   byName.get(name)?.v_nom ?? 0,
        meanPrice: mean,
        peakPrice: n ? peak : 0,
        troughPrice: n ? trough : 0,
      }
    }).sort((a, b) => b.meanPrice - a.meanPrice)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [priceTS, buses, range.from, range.to])

  // Hourly marginal-price chart: either the snapshot-wise mean across all
  // buses (single trace, easy to read at a glance) or every bus individually
  // (multi-trace, useful when locational price spreads matter). We pre-build
  // both shapes once and let the view toggle between them — cheaper than
  // re-shaping the (snapshot × bus) array each time the user clicks.
  const [priceView, setPriceView] = useState<'avg' | 'per_bus'>('avg')
  // Switches between the raw LP dual prices and the merit-order series
  // (LP dual + curtailment_cost of the marginal renewable per bus×snapshot).
  // Defaults to LP — that's PyPSA's actual output and matches the bus table
  // above. Users with curtailment_cost set on renewables can flip to
  // "Merit order" to see prices without the dispatch-subsidy effect.
  const [priceSource, setPriceSource] = useState<'lp' | 'merit'>('lp')
  // Price-drivers panel: threshold (€/MWh) above which we ask the backend to
  // identify the marginal generator + classify the spike. 2000 is a useful
  // default — well above normal merit-order prices, below typical VOLL.
  const [driverThreshold, setDriverThreshold] = useState<number>(2000)
  const { data: priceDrivers } = useQuery({
    queryKey: nk(currentProject, 'results', 'price_drivers', driverThreshold),
    queryFn: () => resultsApi.getPriceDrivers(driverThreshold, 200),
    // Only worth firing when prices exist. Re-runs when threshold changes.
    enabled: !!priceTS,
  })

  // When there are many buses, the legend turns into clutter and the colour
  // palette wraps. Cap the per-bus chart at the top-N by mean |price| —
  // small networks (< cap) render everything; larger ones get the most
  // informative buses surfaced first.
  const PER_BUS_CHART_CAP = 12

  const priceChart = useMemo(() => {
    const tsP = priceTS as PricesPayload | null
    if (!tsP || tsP.columns.length === 0) return null
    // Pick raw LP duals or merit-order grid based on the toggle. Falls
    // back to `data` when `data_adjusted` is missing (older backend).
    const grid = priceSource === 'merit' && tsP.data_adjusted
      ? tsP.data_adjusted
      : tsP.data
    // Snapshot-wise mean across buses. We skip non-finite cells so a single
    // NaN doesn't poison the row average. Scoped to the active period range.
    const rFrom = Math.max(0, range.from)
    const rTo = Math.min(tsP.data.length - 1, range.to)
    const avgRows: Array<{ snapshot: string; avg: number }> = []
    for (let row = rFrom; row <= rTo; row++) {
      let s = 0, n = 0
      for (const v of grid[row]) {
        if (Number.isFinite(v)) { s += v; n += 1 }
      }
      avgRows.push({ snapshot: tsP.index[row], avg: n ? s / n : 0 })
    }
    // Per-bus rows. busStats is already sorted by mean price desc, so the
    // top-N slice picks out the highest-mean-priced buses first.
    const ordered = (busStats ?? []).map(b => b.name)
    const cols = ordered.slice(0, PER_BUS_CHART_CAP)
    const colIdxByName = new Map(tsP.columns.map((c, i) => [c, i]))
    const perBusRows: Array<Record<string, number | string | null>> = []
    for (let row = rFrom; row <= rTo; row++) {
      const obj: Record<string, number | string | null> = { snapshot: tsP.index[row] }
      for (const c of cols) {
        const i = colIdxByName.get(c)
        const v = i != null ? grid[row][i] : null
        obj[c] = Number.isFinite(v as number) ? (v as number) : null
      }
      perBusRows.push(obj)
    }
    return { avgRows, perBusRows, cols, busTotal: tsP.columns.length,
             negativeHours: tsP.negative_hours ?? 0,
             hasAdjusted: !!tsP.data_adjusted }
  }, [priceTS, busStats, priceSource, range.from, range.to])

  // KPIs: peak flow across the network, mean loading, average price
  const kpis = useMemo(() => {
    const peakFlow = lineStats ? Math.max(0, ...lineStats.map(l => l.peak)) : null
    const meanLoading = lineStats && lineStats.length > 0
      ? lineStats.reduce((a, l) => a + l.loadingPct, 0) / lineStats.length : null
    const meanPrice = busStats && busStats.length > 0
      ? busStats.reduce((a, b) => a + b.meanPrice, 0) / busStats.length : null
    return { peakFlow, meanLoading, meanPrice }
  }, [lineStats, busStats])

  const exportLineStatsCSV = () => {
    if (!lineStats) return
    downloadCSV('results_line_stats.csv',
      ['name', 'bus0', 'bus1', 's_nom_MW', 's_nom_opt_MW', 'peak_abs_p0_MW', 'mean_abs_p0_MW', 'peak_loading_pct'],
      lineStats.map(l => [l.name, l.bus0, l.bus1, l.s_nom, l.s_nom_opt, l.peak.toFixed(3), l.mean.toFixed(3), l.loadingPct.toFixed(2)]),
    )
    toast.success(`Exported ${lineStats.length} rows`)
  }

  const exportTrafoStatsCSV = () => {
    if (!trafoStats) return
    downloadCSV('results_transformer_stats.csv',
      ['name', 'bus0', 'bus1', 's_nom_MVA', 's_nom_opt_MVA', 'peak_abs_p0_MW', 'mean_abs_p0_MW', 'peak_loading_pct'],
      trafoStats.map(t => [t.name, t.bus0, t.bus1, t.s_nom, t.s_nom_opt, t.peak.toFixed(3), t.mean.toFixed(3), t.loadingPct.toFixed(2)]),
    )
    toast.success(`Exported ${trafoStats.length} rows`)
  }

  const exportBusStatsCSV = () => {
    if (!busStats) return
    downloadCSV('results_bus_stats.csv',
      ['name', 'carrier', 'v_nom_kV', 'mean_price_EUR_per_MWh', 'peak_price', 'trough_price'],
      busStats.map(b => [b.name, b.carrier, b.v_nom, b.meanPrice.toFixed(3), b.peakPrice.toFixed(3), b.troughPrice.toFixed(3)]),
    )
    toast.success(`Exported ${busStats.length} rows`)
  }

  if (!tsLines && !priceTS) {
    return (
      <div className="p-6 text-center text-xs text-muted">
        No load-flow results yet. Run a simulation (any mode produces line flows; LOPF additionally produces marginal prices).
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-5 p-5 overflow-y-auto h-full text-sm [&>*]:shrink-0">

      {/* ── KPI strip ─────────────────────────────────────────── */}
      <section>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">Network</h3>
          {/* Result-source toggle. Hidden when Stage 2 hasn't run yet — the
              backend would just return the same LOPF data either way, so
              giving the user a no-op pill would be misleading. Same store
              backs the canvas overlay (CanvasResultsContext). */}
          {acPfAvailable && (
            <div className="flex border border-border rounded overflow-hidden text-[11px]">
              <button
                onClick={() => setResultSource('lopf')}
                className={`px-2.5 py-1 transition-colors ${
                  resultSource === 'lopf' ? 'bg-accent text-white' : 'bg-bg text-muted hover:text-text'
                }`}
                title="LOPF (linear DC-OPF) results"
              >LOPF</button>
              <button
                onClick={() => setResultSource('ac_pf')}
                className={`px-2.5 py-1 transition-colors ${
                  resultSource === 'ac_pf' ? 'bg-accent text-white' : 'bg-bg text-muted hover:text-text'
                }`}
                title={`AC Power Flow results (${acPfStatus?.converged_count ?? 0}/${acPfStatus?.total_snapshots ?? 0} converged)`}
              >AC PF</button>
            </div>
          )}
        </div>
        <div className="grid grid-cols-2 gap-2">
          <KPI label="Peak flow"
               value={fmtPower(kpis.peakFlow)}
               hint="Maximum |p0| observed across all lines and snapshots in the active range." />
          <KPI label="Mean loading"
               value={kpis.meanLoading != null ? `${kpis.meanLoading.toFixed(1)} %` : '—'}
               hint="Average of per-line peak loading %. See the Prices tab for marginal-price KPIs." />
        </div>
      </section>

      {/* ── Time-series view mode (weekly / monthly / full timeframe) ─────── */}
      {/* Applies to the voltage, top-loaded lines, and top-loaded transformers
          charts below. Weekly is the default; it greys out (falling back to
          monthly, then timeframe) when the dataset doesn't span enough of
          each season. */}
      <section className="flex items-center gap-2 flex-wrap">
        <span className="text-[11px] font-medium text-text">Time-series view</span>
        <Seg
          value={lfMode}
          onChange={setLoadFlowViewMode}
          options={[
            { value: 'weekly', label: 'Weekly', disabled: !lfWeeklyOk,
              title: lfWeeklyOk
                ? 'Averaged week per season — 4 charts (Mon–Sun, 168 h)'
                : `Greyed out — needs ≥ ${WEEKLY_MIN_DAYS} days of data in all 4 seasons` },
            { value: 'monthly', label: 'Monthly', disabled: !lfMonthlyOk,
              title: lfMonthlyOk
                ? 'Averaged month per season — 4 charts'
                : `Greyed out — needs ≥ ${MONTHLY_MIN_DAYS} days of data in all 4 seasons` },
            { value: 'timeframe', label: 'Full timeframe',
              title: 'The full dataset — single hourly chart per section' },
          ]}
        />
        <span className="text-[10px] text-muted">
          {lfMode === 'weekly'    && 'One averaged week per season (Winter / Spring / Summer / Autumn).'}
          {lfMode === 'monthly'   && 'One averaged month per season (Winter / Spring / Summer / Autumn).'}
          {lfMode === 'timeframe' && 'Hourly charts over the full selected horizon.'}
        </span>
      </section>

      {/* CO₂ emissions section moved to the dedicated "Emissions" tab.
          Disabled via SHOW_RETIRED_SECTIONS (see note at top of file). */}
      {SHOW_RETIRED_SECTIONS && emissions && (
        <section>
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">CO₂ emissions</h3>
            {emissions.cap.active && emissions.cap.cap_tCO2 != null && (
              <span className="text-[10px] text-muted">
                Active cap: <span className="font-mono text-text">{emissions.cap.cap_tCO2.toLocaleString(undefined, { maximumFractionDigits: 1 })} tCO₂</span>
                {' '}
                ({(emissions.cap.name ?? '')})
              </span>
            )}
          </div>
          <div className={`grid gap-2 ${emissions.cap.active ? 'grid-cols-3' : 'grid-cols-1'}`}>
            <KPI label="Total emissions"
                 value={`${emissions.total_tCO2.toLocaleString(undefined, { maximumFractionDigits: 1 })} tCO₂`} />
            {emissions.cap.active && (
              <>
                <KPI label="Shadow price (marginal abatement)"
                     value={emissions.cap.shadow_price_eur_per_tCO2 != null
                       ? `${emissions.cap.shadow_price_eur_per_tCO2.toFixed(2)} €/tCO₂`
                       : '—'} />
                <KPI label="Cap slack"
                     value={emissions.cap.slack_tCO2 != null
                       ? `${emissions.cap.slack_tCO2.toLocaleString(undefined, { maximumFractionDigits: 1 })} tCO₂`
                       : '—'} />
              </>
            )}
          </div>
          {emissions.total_tCO2 === 0 ? (
            <p className="text-[11px] text-muted mt-2">
              All dispatched carriers have <code>co2_emissions = 0</code>. Set per-carrier
              emission intensities (tCO₂/MWh of primary energy) in the Carriers tab to
              activate this report.
            </p>
          ) : emissions.by_carrier.length > 0 && (
            <div className="mt-2 border border-border rounded overflow-auto max-h-64">
              <table className="w-full text-xs border-collapse" style={{ minWidth: 560 }}>
                <thead className="sticky top-0 bg-bg-2 z-10">
                  <tr className="border-b border-border">
                    <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Carrier</th>
                    <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">tCO₂</th>
                    <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Share</th>
                  </tr>
                </thead>
                <tbody>
                  {emissions.by_carrier.map((row, i) => (
                    <tr key={row.carrier} className={i % 2 === 0 ? 'bg-bg' : 'bg-panel'}>
                      <td className="px-2 py-1 font-mono text-[11px]">{row.carrier || '(blank)'}</td>
                      <td className="px-2 py-1 font-mono text-[11px] text-right">{row.tCO2.toLocaleString(undefined, { maximumFractionDigits: 2 })}</td>
                      <td className="px-2 py-1 font-mono text-[11px] text-right">{row.share_pct.toFixed(1)} %</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {/* Per-generator table (collapsed by default to keep the panel scannable). */}
          {emissions.by_generator.filter(r => r.tCO2 > 0).length > 0 && (
            <details className="mt-2">
              <summary className="text-[10px] text-muted hover:text-accent cursor-pointer select-none">
                Per-generator breakdown ({emissions.by_generator.filter(r => r.tCO2 > 0).length} emitters)
              </summary>
              <div className="mt-1 border border-border rounded overflow-auto max-h-64">
                <table className="w-full text-xs border-collapse" style={{ minWidth: 560 }}>
                  <thead className="sticky top-0 bg-bg-2 z-10">
                    <tr className="border-b border-border">
                      <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Generator</th>
                      <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Carrier</th>
                      <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Energy (MWh)</th>
                      <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">tCO₂</th>
                      <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Intensity (tCO₂/MWhₑ)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {emissions.by_generator.filter(r => r.tCO2 > 0).map((row, i) => (
                      <tr key={row.name} className={i % 2 === 0 ? 'bg-bg' : 'bg-panel'}>
                        <td className="px-2 py-1 font-mono text-[11px]">{row.name}</td>
                        <td className="px-2 py-1 text-[11px] text-muted">{row.carrier || '(blank)'}</td>
                        <td className="px-2 py-1 font-mono text-[11px] text-right">{row.energy_mwh.toLocaleString(undefined, { maximumFractionDigits: 1 })}</td>
                        <td className="px-2 py-1 font-mono text-[11px] text-right">{row.tCO2.toLocaleString(undefined, { maximumFractionDigits: 2 })}</td>
                        <td className="px-2 py-1 font-mono text-[11px] text-right">{row.intensity_tCO2_per_MWh_out.toFixed(3)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          )}
        </section>
      )}

      {/* ── Per-carrier KPIs ────────────────────────────────── */}
      {/* Wraps PyPSA's n.statistics.capacity_factor / curtailment /
          market_value / revenue helpers grouped by carrier. The four headline
          questions every LOPF report needs to answer: how much was each
          carrier dispatching, how much got left on the table, what price did
          they fetch, how much money did they make. */}
      {carrierKpis && carrierKpis.rows.length > 0 && (
        <section>
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">Carrier KPIs ({carrierKpis.rows.length})</h3>
            <button
              onClick={() => {
                if (!carrierKpis) return
                downloadCSV('results_carrier_kpis.csv',
                  ['component', 'carrier', 'capacity_MW', 'energy_MWh', 'capacity_factor_pct', 'curtailment_MWh', 'curtailment_pct', 'market_value_EUR_per_MWh', 'revenue_EUR'],
                  carrierKpis.rows.map(r => [
                    r.component, r.carrier,
                    r.capacity_mw.toFixed(2),
                    r.energy_mwh.toFixed(2),
                    r.capacity_factor_pct.toFixed(2),
                    r.curtailment_mwh.toFixed(2),
                    r.curtailment_pct.toFixed(2),
                    r.market_value_eur_per_mwh.toFixed(3),
                    r.revenue_eur.toFixed(2),
                  ]),
                )
                toast.success(`Exported ${carrierKpis.rows.length} rows`)
              }}
              className="flex items-center gap-1 text-[11px] text-muted hover:text-accent"
            ><Download size={11} /> CSV</button>
          </div>
          <div className="border border-border rounded overflow-auto max-h-72">
            <table className="w-full text-xs border-collapse" style={{ minWidth: 760 }}>
              <thead className="sticky top-0 bg-bg-2 z-10">
                <tr className="border-b border-border">
                  <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Component</th>
                  <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Carrier</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase" title="Optimal capacity (post-solve) or installed capacity (input).">Capacity (MW)</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase" title="Energy dispatched (sum p × snapshot_weighting). PyPSA: n.statistics.supply.">Energy (MWh)</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase" title="energy / (capacity × hours_in_horizon). PyPSA: n.statistics.capacity_factor.">CF (%)</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase" title="(max-available − dispatched). Most meaningful for renewables; for storage it includes idle hours.">Curtailment (MWh)</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Curt %</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase" title="revenue / energy. The €/MWh price the carrier received. PyPSA: n.statistics.market_value.">Market value (€/MWh)</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase" title="Σ (p × marginal_price × weight). PyPSA: n.statistics.revenue. Can be negative for off-design behaviour.">Revenue (€)</th>
                </tr>
              </thead>
              <tbody>
                {carrierKpis.rows.map((r, i) => (
                  <tr key={`${r.component}-${r.carrier}`} className={i % 2 === 0 ? 'bg-bg' : 'bg-panel'}>
                    <td className="px-2 py-1 text-[11px] text-muted">{r.component}</td>
                    <td className="px-2 py-1 text-[11px]">{r.carrier || '(blank)'}</td>
                    <td className="px-2 py-1 font-mono text-[11px] text-right">{r.capacity_mw.toLocaleString(undefined, { maximumFractionDigits: 1 })}</td>
                    <td className="px-2 py-1 font-mono text-[11px] text-right">{r.energy_mwh.toLocaleString(undefined, { maximumFractionDigits: 1 })}</td>
                    <td className="px-2 py-1 font-mono text-[11px] text-right">{r.capacity_factor_pct.toFixed(1)}</td>
                    <td className="px-2 py-1 font-mono text-[11px] text-right">{r.curtailment_mwh.toLocaleString(undefined, { maximumFractionDigits: 1 })}</td>
                    <td className={`px-2 py-1 font-mono text-[11px] text-right ${r.curtailment_pct > 50 ? 'text-warn' : ''}`}>{r.curtailment_pct.toFixed(1)}</td>
                    <td className="px-2 py-1 font-mono text-[11px] text-right">{r.market_value_eur_per_mwh.toFixed(2)}</td>
                    <td className={`px-2 py-1 font-mono text-[11px] text-right ${r.revenue_eur < 0 ? 'text-danger' : ''}`}>{r.revenue_eur.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-[10px] text-muted mt-1.5">
            <strong>CF</strong>: dispatched energy / nameplate × hours. <strong>Curtailment</strong>:
            for renewables, the energy <em>could</em> have been generated but wasn't; for storage, includes idle hours
            so the number inflates and is less directly meaningful. <strong>Market value</strong> = revenue / energy.
            <strong>Revenue</strong>: Σ p × marginal_price × weight.
          </p>
        </section>
      )}

      {/* ── Line congestion shadow prices ────────────────────── */}
      {/* LP duals on the line capacity constraints. Answers the canonical
          grid-planning question: which corridor would benefit most from
          additional capacity? Hidden when no duals were captured (e.g. on
          an old solve produced before assign_all_duals was wired). */}
      {lineDuals && lineDuals.rows.length > 0 && (
        <section>
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">Line congestion (LP duals)</h3>
            {lineDuals.total_congestion_rent_eur > 0 && (
              <span className="text-[10px] text-muted">
                Total congestion rent:{' '}
                <span className="font-mono text-text">
                  €{lineDuals.total_congestion_rent_eur.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                </span>
              </span>
            )}
          </div>
          {lineDuals.note && (
            <p className="text-[10px] text-warn mb-2">{lineDuals.note}</p>
          )}
          <div className="border border-border rounded overflow-auto max-h-72">
            <table className="w-full text-xs border-collapse" style={{ minWidth: 680 }}>
              <thead className="sticky top-0 bg-bg-2 z-10">
                <tr className="border-b border-border">
                  <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Line</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">s_nom (MW)</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase" title="Snapshots where the line was at +/- capacity (|mu| > 1e-6).">Binding</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase" title="Peak shadow price across all snapshots. The most an extra MW of capacity would be worth at any single hour.">Max mu (€/MWh)</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase" title="Average shadow price across binding snapshots only.">Mean mu when binding</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase" title="Σ |mu| × |p0| × weight. The LP-derived welfare value of redispatching out of this congestion.">Congestion rent (€)</th>
                </tr>
              </thead>
              <tbody>
                {lineDuals.rows.map((r, i) => {
                  const pctBinding = lineDuals.n_snapshots > 0
                    ? (100 * r.binding_hours / lineDuals.n_snapshots).toFixed(0)
                    : '0'
                  const vollHours = r.voll_bound_hours ?? 0
                  const vollDominated = vollHours > 0 && vollHours >= r.binding_hours * 0.5
                  return (
                    <tr key={r.name} className={i % 2 === 0 ? 'bg-bg' : 'bg-panel'}>
                      <td className="px-2 py-1 font-mono text-[11px]">{r.name}</td>
                      <td className="px-2 py-1 font-mono text-[11px] text-right">{r.s_nom_MW.toLocaleString(undefined, { maximumFractionDigits: 1 })}</td>
                      <td className={`px-2 py-1 font-mono text-[11px] text-right ${r.binding_hours > 0 ? 'text-warn font-semibold' : ''}`}>
                        {r.binding_hours} / {lineDuals.n_snapshots}{' '}
                        <span className="opacity-60">({pctBinding}%)</span>
                        {vollHours > 0 && (
                          <span className="ml-1 text-[9px] text-danger" title={`${vollHours} of those snapshots had |mu| ≥ 10,000 €/MWh — typically VOLL-priced load shedding, not transmission scarcity. The line is at capacity because demand was being shed, not because more wires would help.`}>
                            · VOLL: {vollHours}
                          </span>
                        )}
                      </td>
                      <td className="px-2 py-1 font-mono text-[11px] text-right">{r.max_mu_eur_per_MWh.toLocaleString(undefined, { maximumFractionDigits: 2 })}</td>
                      <td className="px-2 py-1 font-mono text-[11px] text-right">{r.mean_mu_when_binding_eur_per_MWh.toLocaleString(undefined, { maximumFractionDigits: 2 })}</td>
                      <td className={`px-2 py-1 font-mono text-[11px] text-right ${r.congestion_rent_eur > 0 ? 'font-semibold' : ''} ${vollDominated ? 'text-danger' : ''}`}
                          title={vollDominated ? 'Most of this rent is from VOLL-priced shedding hours — not a real transmission expansion signal.' : undefined}>
                        {r.congestion_rent_eur.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <p className="text-[10px] text-muted mt-1.5">
            Shadow prices are the LP duals of the s_nom capacity constraint — €/MWh marginal
            benefit of one additional MW of line capacity. A line that binds at €1000/MWh in
            46 hours is roughly worth €46k/MW-year more capacity than one that never binds.
          </p>
        </section>
      )}

      {/* ── Unit commitment ───────────────────────────────── */}
      {/* Per-generator UC stats from generators_t.status / start_up / shut_down.
          Hidden when no committable units exist. The heatmap visualises the
          binary on/off matrix — quickly answers "did the LP cycle this peaker
          a lot?" and "are min-up-time / min-down-time being respected?". */}
      {ucResults && ucResults.n_committable > 0 && ucResults.generators.length > 0 && (
        <section>
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">Unit Commitment ({ucResults.n_committable})</h3>
            <button
              onClick={() => {
                if (!ucResults) return
                downloadCSV('results_unit_commitment.csv',
                  ['name', 'carrier', 'p_nom_MW', 'n_starts', 'n_shuts', 'hours_on', 'energy_MWh', 'cf_when_on_pct', 'uc_cost_EUR'],
                  ucResults.generators.map(g => [
                    g.name, g.carrier, g.p_nom_MW.toFixed(2),
                    g.n_starts, g.n_shuts, g.hours_on.toFixed(0),
                    g.energy_mwh.toFixed(2),
                    g.capacity_factor_when_on_pct.toFixed(2),
                    g.total_uc_cost_eur.toFixed(2),
                  ]),
                )
                toast.success(`Exported ${ucResults.generators.length} rows`)
              }}
              className="flex items-center gap-1 text-[11px] text-muted hover:text-accent"
            ><Download size={11} /> CSV</button>
          </div>
          {ucResults.note && (
            <p className="text-[10px] text-warn mb-2">{ucResults.note}</p>
          )}
          <div className="border border-border rounded overflow-auto max-h-64">
            <table className="w-full text-xs border-collapse" style={{ minWidth: 720 }}>
              <thead className="sticky top-0 bg-bg-2 z-10">
                <tr className="border-b border-border">
                  <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Generator</th>
                  <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Carrier</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">p_nom (MW)</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Starts</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Shuts</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Hours on</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase" title="Energy dispatched / (p_nom × hours_on). The operational CF when the unit was committed.">CF when on</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase" title="start_up_cost × n_starts + shut_down_cost × n_shuts">UC cost (€)</th>
                </tr>
              </thead>
              <tbody>
                {ucResults.generators.map((g, i) => (
                  <tr key={g.name} className={i % 2 === 0 ? 'bg-bg' : 'bg-panel'}>
                    <td className="px-2 py-1 font-mono text-[11px]">{g.name}</td>
                    <td className="px-2 py-1 text-[11px] text-muted">{g.carrier || '(blank)'}</td>
                    <td className="px-2 py-1 font-mono text-[11px] text-right">{g.p_nom_MW.toLocaleString(undefined, { maximumFractionDigits: 1 })}</td>
                    <td className={`px-2 py-1 font-mono text-[11px] text-right ${g.n_starts > 10 ? 'text-warn font-semibold' : ''}`}>{g.n_starts}</td>
                    <td className={`px-2 py-1 font-mono text-[11px] text-right ${g.n_shuts > 10 ? 'text-warn font-semibold' : ''}`}>{g.n_shuts}</td>
                    <td className="px-2 py-1 font-mono text-[11px] text-right">{g.hours_on.toFixed(0)}</td>
                    <td className="px-2 py-1 font-mono text-[11px] text-right">{g.capacity_factor_when_on_pct.toFixed(1)} %</td>
                    <td className="px-2 py-1 font-mono text-[11px] text-right">{g.total_uc_cost_eur.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {/* Status heatmap. Each row = one committable generator; each
              cell = one snapshot. Green = on, grey = off. Caps at 24 cells/row
              for visibility on the dashboard; full grid is in the CSV. */}
          {ucResults.status_grid && ucResults.status_grid.columns.length > 0 && (() => {
            const grid = ucResults.status_grid
            // Range-aware: slice the grid's index / data by the active
            // horizon filter so a per-period view shows that period's
            // commit pattern instead of the whole solve. The stride is
            // computed against the SLICED length so the displayed
            // density matches whatever window the user is looking at.
            const lastRow = grid.index.length - 1
            const rFrom = Math.max(0, Math.min(range.from, lastRow))
            const rTo   = Math.max(0, Math.min(range.to,   lastRow))
            const sliceLen = Math.max(0, rTo - rFrom + 1)
            const TARGET_COLS = 168  // ~ 1 week at hourly
            const stride = Math.max(1, Math.floor(sliceLen / TARGET_COLS))
            // Absolute indices into `grid.data` / `grid.index`, sampled
            // by stride within the range slice.
            const sampledIndices = Array.from(
              { length: Math.ceil(sliceLen / stride) },
              (_, i) => rFrom + i * stride,
            )
            return (
              <div className="mt-3">
                <p className="text-[10px] text-muted mb-1">
                  On/off timeline ({sliceLen} snapshot{sliceLen !== 1 ? 's' : ''} in view
                  {stride > 1 ? `, showing every ${stride}-th` : ''})
                </p>
                <div className="border border-border rounded overflow-auto">
                  <table className="text-[10px] font-mono" style={{ borderSpacing: 0 }}>
                    <tbody>
                      {grid.columns.map((gen, gi) => (
                        <tr key={gen}>
                          <td className="px-2 py-0.5 sticky left-0 bg-bg border-r border-border text-[10px] whitespace-nowrap" title={gen}>
                            {gen.length > 18 ? gen.slice(0, 16) + '…' : gen}
                          </td>
                          {sampledIndices.map(ti => {
                            const v = grid.data[ti]?.[gi] ?? 0
                            const bg = v > 0.5 ? '#16a34a' : '#e5e7eb'
                            return (
                              <td key={ti} title={`${grid.index[ti]}: ${v > 0.5 ? 'on' : 'off'}`}
                                  style={{ background: bg, width: 3, height: 12, padding: 0 }} />
                            )
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="text-[10px] text-muted mt-1">
                  Green = on, grey = off. Many short on-off cycles suggest tight min-up/min-down
                  time constraints or marginal economics — review <code>start_up_cost</code> and
                  <code>min_up_time</code> on those generators.
                </p>
              </div>
            )
          })()}
        </section>
      )}

      {/* ── Transmission losses summary ──────────────────────── */}
      {/* Always rendered when a solved network is available; shows zeros +
          hint when transmission_losses was off on the last run. The
          per-branch table only appears when there's data to show. */}
      <section>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">Transmission losses</h3>
          {lossesData && !lossesData.enabled && (
            <span className="text-[10px] text-muted">
              Losses were not modelled in the last solve (transmission_losses=off) — totals are zero.
            </span>
          )}
        </div>
        <div className="grid grid-cols-3 gap-2">
          <KPI label="Total energy lost"
            value={lossesData ? `${lossesData.total_mwh.toLocaleString(undefined, { maximumFractionDigits: 2 })} MWh` : '—'} />
          <KPI label="Peak loss"
            value={lossesData ? `${lossesData.peak_mw.toLocaleString(undefined, { maximumFractionDigits: 2 })} MW` : '—'} />
          <KPI label="% of demand"
            value={lossesData ? `${lossesData.loss_pct_of_demand.toFixed(3)} %` : '—'} />
        </div>
        {lossesData && lossesData.by_branch.length > 0 && lossesData.total_mwh > 0 && (
          <div className="mt-2 border border-border rounded overflow-auto max-h-48">
            <table className="w-full text-xs border-collapse">
              <thead className="sticky top-0 bg-bg-2 z-10">
                <tr className="border-b border-border">
                  <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Component</th>
                  <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Name</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Loss (MWh)</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Peak (MW)</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Share</th>
                </tr>
              </thead>
              <tbody>
                {lossesData.by_branch.map(r => (
                  <tr key={`${r.component}-${r.name}`} className="border-b border-border last:border-b-0 hover:bg-panel">
                    <td className="px-2 py-1 text-muted">{r.component}</td>
                    <td className="px-2 py-1">{r.name}</td>
                    <td className="px-2 py-1 text-right font-mono">{r.loss_mwh.toLocaleString(undefined, { maximumFractionDigits: 3 })}</td>
                    <td className="px-2 py-1 text-right font-mono">{r.peak_mw.toLocaleString(undefined, { maximumFractionDigits: 3 })}</td>
                    <td className="px-2 py-1 text-right font-mono">{r.share_pct.toFixed(1)} %</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ── LP-modelled vs Real (AC PF) losses comparison ────── */}
      {/* Only when Stage 2 is available — without it there's no comparison
          to make. Shows side-by-side totals so the user sees at a glance
          how good the LP's loss approximation was vs the physical reality
          PyPSA's Newton-Raphson computed. */}
      {acPfAvailable && lossesLopf && lossesAcPf && (
        <section>
          <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em] mb-2">Loss comparison — LP model vs AC PF</h3>
          <div className="grid grid-cols-2 gap-2">
            <div className="border border-border rounded p-2">
              <p className="text-[10px] text-muted uppercase tracking-wider mb-1">LOPF (LP approximation)</p>
              {lossesLopf.enabled ? (
                <>
                  <p className="text-base font-mono text-text">{lossesLopf.total_mwh.toLocaleString(undefined, { maximumFractionDigits: 3 })} MWh</p>
                  <p className="text-[10px] text-muted">{lossesLopf.loss_pct_of_demand.toFixed(3)} % of demand · peak {lossesLopf.peak_mw.toFixed(3)} MW</p>
                </>
              ) : (
                <p className="text-[11px] text-muted italic">transmission_losses was off; LP modelled no losses.</p>
              )}
            </div>
            <div className="border border-border rounded p-2">
              <p className="text-[10px] text-muted uppercase tracking-wider mb-1">AC PF (real losses)</p>
              <p className="text-base font-mono text-text">{lossesAcPf.total_mwh.toLocaleString(undefined, { maximumFractionDigits: 3 })} MWh</p>
              <p className="text-[10px] text-muted">{lossesAcPf.loss_pct_of_demand.toFixed(3)} % of demand · peak {lossesAcPf.peak_mw.toFixed(3)} MW</p>
            </div>
          </div>
          {lossesLopf.enabled && lossesAcPf.total_mwh > 0 && (
            <p className="text-[10px] text-muted mt-1.5">
              Approximation error:{' '}
              <span className="font-mono">
                {((lossesLopf.total_mwh - lossesAcPf.total_mwh) / lossesAcPf.total_mwh * 100).toFixed(1)} %
              </span>{' '}
              ({lossesLopf.total_mwh > lossesAcPf.total_mwh ? 'over' : 'under'}-estimated by the LP)
            </p>
          )}
        </section>
      )}

      {/* ── AC Power Flow status section ──────────────────────── */}
      {/* Surface convergence + slack info from the dedicated /ac_pf/status
          endpoint. Hidden when Stage 2 hasn't run since the last solve. */}
      {acPfStatus?.available && (
        <section>
          <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em] mb-2">AC Power Flow status</h3>
          <div className="grid grid-cols-3 gap-2">
            <KPI label="Snapshots converged"
              value={`${acPfStatus.converged_count} / ${acPfStatus.total_snapshots}`} />
            <KPI label="Slack bus" value={acPfStatus.slack_bus_used ?? '—'} />
            <KPI label="VOLL slacks stripped"
              value={`${(acPfStatus.stripped_voll_slacks ?? []).length}`} />
          </div>
          {/* List of non-converged snapshots — clickable to jump the canvas's
              snapshot picker there so the user can inspect the LP fallback. */}
          {(() => {
            // Prefer the period-aware `converged_list` (multi-period safe). The
            // legacy `converged_per_snapshot` dict can't disambiguate same-iso
            // entries from different periods — indexOf would always pick
            // period 0's row. The list carries `period` so we can match both.
            type ConvRow = { snapshot: string; period?: number | string | null; ok: boolean }
            const list = (acPfStatus.converged_list ?? []) as ConvRow[]
            const failedList: ConvRow[] = list.length > 0
              ? list.filter(r => !r.ok)
              : Object.entries(acPfStatus.converged_per_snapshot ?? {})
                  .filter(([, ok]) => !ok)
                  .map(([iso]) => ({ snapshot: iso, ok: false }))
            if (failedList.length === 0) {
              return <p className="text-[11px] text-muted mt-2">All snapshots converged.</p>
            }
            // Build a lookup keyed by `period|iso` (or just iso for flat) so
            // the jump button finds the exact row in n.snapshots — multi-period
            // can't rely on indexOf alone because the same iso appears once
            // per period.
            const snapshots = snap?.snapshots ?? []
            const periodsArr = snap?.periods
            const keyForSnap = (iso: string, period: number | string | null | undefined) =>
              period == null ? iso : `${period}|${iso}`
            const snapIndexByKey = new Map<string, number>()
            for (let i = 0; i < snapshots.length; i++) {
              const p = periodsArr?.[i]
              snapIndexByKey.set(keyForSnap(snapshots[i], p ?? null), i)
            }
            return (
              <div className="mt-2 border border-border rounded p-2 max-h-32 overflow-auto">
                <p className="text-[10px] text-muted uppercase tracking-wider mb-1">
                  {failedList.length} non-converged snapshot{failedList.length === 1 ? '' : 's'}
                </p>
                <div className="flex flex-wrap gap-1">
                  {failedList.map((r, i) => {
                    const key = keyForSnap(r.snapshot, r.period ?? null)
                    const idx = snapIndexByKey.get(key) ?? -1
                    const label = r.period != null
                      ? `${r.period} · ${shortStamp(r.snapshot)}`
                      : shortStamp(r.snapshot)
                    return (
                      <button
                        key={`${key}-${i}`}
                        onClick={() => idx >= 0 && setResultsSnapshotIdx(idx)}
                        disabled={idx < 0}
                        className="text-[10px] font-mono px-1.5 py-0.5 rounded border border-danger/40 text-danger hover:bg-danger/10 disabled:opacity-40"
                        title={`Jump to snapshot ${r.snapshot}${r.period != null ? ` (period ${r.period})` : ''}`}
                      >
                        {label}
                      </button>
                    )
                  })}
                </div>
              </div>
            )
          })()}
        </section>
      )}

      {/* ── Bus voltage (AC PF only) ──────────────────────────── */}
      {/* Only rendered when Stage 2 produced voltage data. LP stage leaves
          v_mag_pu at its default 1.0 for every bus, which would make this
          panel uninformative (and misleading) on a LOPF-only run. */}
      {acPfAvailable && busVoltageStats && busVoltageStats.length > 0 && (
        <section>
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">Bus voltage · AC PF ({busVoltageStats.length})</h3>
            <button
              onClick={() => {
                if (!busVoltageStats) return
                downloadCSV('results_bus_voltage.csv',
                  ['name', 'carrier', 'v_nom_kV', 'min_pu', 'max_pu', 'mean_pu'],
                  busVoltageStats.map(b => [b.name, b.carrier, b.v_nom, b.min.toFixed(5), b.max.toFixed(5), b.mean.toFixed(5)]),
                )
                toast.success(`Exported ${busVoltageStats.length} rows`)
              }}
              className="flex items-center gap-1 text-[11px] text-muted hover:text-accent"
            ><Download size={11} /> CSV</button>
          </div>
          <div className="border border-border rounded overflow-auto max-h-64 mb-2">
            <table className="w-full text-xs border-collapse" style={{ minWidth: 540 }}>
              <thead className="sticky top-0 bg-bg-2 z-10">
                <tr className="border-b border-border">
                  <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Bus</th>
                  <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Carrier</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">v_nom (kV)</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Min (p.u.)</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Max (p.u.)</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Mean (p.u.)</th>
                </tr>
              </thead>
              <tbody>
                {busVoltageStats.map((b, i) => {
                  const outOfBand = b.min < 0.95 || b.max > 1.05
                  return (
                    <tr key={b.name} className={i % 2 === 0 ? 'bg-bg' : 'bg-panel'}>
                      <td className="px-2 py-1 font-mono text-[11px]">{b.name}</td>
                      <td className="px-2 py-1 text-[11px] text-muted">{b.carrier}</td>
                      <td className="px-2 py-1 font-mono text-[11px] text-right">{b.v_nom}</td>
                      <td className={`px-2 py-1 font-mono text-[11px] text-right ${b.min < 0.95 ? 'text-danger font-semibold' : ''}`}>{b.min.toFixed(4)}</td>
                      <td className={`px-2 py-1 font-mono text-[11px] text-right ${b.max > 1.05 ? 'text-danger font-semibold' : ''}`}>{b.max.toFixed(4)}</td>
                      <td className={`px-2 py-1 font-mono text-[11px] text-right ${outOfBand ? 'text-warn' : ''}`}>{b.mean.toFixed(4)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {voltageChart && voltageChart.cols.length > 0 && (
            <div>
              <div className="flex items-center justify-end mb-1">
                <ChartActions
                  chartRef={voltageChartRef}
                  onExportCSV={() => {
                    const header = ['snapshot', ...voltageChart.cols]
                    downloadCSV('results_bus_voltage.csv', header,
                      voltageChart.rows.map(r =>
                        [r.snapshot, ...voltageChart.cols.map(c => r[c] ?? '')]))
                    toast.success('Exported')
                  }}
                  filename="results_bus_voltage" />
              </div>
              <div ref={voltageChartRef}>
                {lfMode !== 'timeframe' ? (
                  <SeasonalLineCardGrid
                    mode={lfMode}
                    rows={voltageChart.rows}
                    cols={voltageChart.cols}
                    colors={['#dc2626', '#0ea5e9', '#16a34a', '#d97706', '#7c3aed', '#db2777', '#0891b2', '#65a30d']}
                    unit="p.u."
                    valueFormatter={(v: number) => `${v.toFixed(4)} p.u.`}
                    height={170}
                  />
                ) : (
                  <ResponsiveContainer width="100%" height={200}>
                    <LineChart data={voltageChart.rows} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                      <CartesianGrid {...CHART_GRID} />
                      <XAxis dataKey="snapshot" tickFormatter={shortStamp} {...CHART_AXIS} />
                      <YAxis {...CHART_AXIS} domain={['auto', 'auto']}
                        label={yAxisLabel('p.u.')} />
                      <RTooltip {...CHART_TOOLTIP} labelFormatter={shortStamp} formatter={(v: number) => `${v.toFixed(4)} p.u.`} />
                      <Legend {...CHART_LEGEND} />
                      {voltageChart.cols.map((c, i) => (
                        <Line key={c} type="monotone" dataKey={c} name={c}
                              stroke={['#dc2626', '#0ea5e9', '#16a34a', '#d97706', '#7c3aed', '#db2777', '#0891b2', '#65a30d'][i % 8]}
                              strokeWidth={1.4} dot={false} />
                      ))}
                    </LineChart>
                  </ResponsiveContainer>
                )}
              </div>
            </div>
          )}
          {voltageChart && voltageChart.busTotal > voltageChart.cols.length && (
            <p className="text-[10px] text-muted mt-1">
              Showing top {voltageChart.cols.length} of {voltageChart.busTotal} buses by deviation from 1.0 p.u.
            </p>
          )}
        </section>
      )}

      {/* ── Lines table ───────────────────────────────────────── */}
      <section>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">Lines ({lineStats?.length ?? 0})</h3>
          <button onClick={exportLineStatsCSV} disabled={!lineStats}
            className="flex items-center gap-1 text-[11px] text-muted hover:text-accent disabled:opacity-40"
          ><Download size={11} /> CSV</button>
        </div>
        {!lineStats ? (
          <div className="border border-border rounded p-4 text-center text-xs text-muted">
            No line flow time-series.
          </div>
        ) : (
          <div className="border border-border rounded overflow-auto max-h-72">
            <table className="w-full text-xs border-collapse" style={{ minWidth: 720 }}>
              <thead className="sticky top-0 bg-bg-2 z-10">
                <tr className="border-b border-border">
                  <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Name</th>
                  <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">From</th>
                  <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">To</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase" title="Original (pre-solve) thermal rating">s_nom</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase" title="Optimized capacity from the LP. Loading% is computed against this — so 100% on a line whose s_nom_opt > s_nom means the LP expanded it.">s_nom_opt</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Peak |p|</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Mean |p|</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Loading</th>
                </tr>
              </thead>
              <tbody>
                {lineStats.map((l, i) => {
                  const expanded = l.s_nom_opt > l.s_nom + 1e-6
                  return (
                    <tr key={l.name} className={i % 2 === 0 ? 'bg-bg' : 'bg-panel'}>
                      <td className="px-2 py-1 font-mono text-[11px]">{l.name}</td>
                      <td className="px-2 py-1 text-[11px]">{l.bus0}</td>
                      <td className="px-2 py-1 text-[11px]">{l.bus1}</td>
                      <td className="px-2 py-1 font-mono text-[11px] text-right">{fmtPower(l.s_nom, 1)}</td>
                      <td className={`px-2 py-1 font-mono text-[11px] text-right ${expanded ? 'text-accent font-semibold' : ''}`}
                          title={expanded ? `Expanded by ${fmtPower(l.s_nom_opt - l.s_nom, 1)}` : 'No expansion'}>
                        {fmtPower(l.s_nom_opt, 1)}
                      </td>
                      <td className="px-2 py-1 font-mono text-[11px] text-right">{fmtPower(l.peak, 1)}</td>
                      <td className="px-2 py-1 font-mono text-[11px] text-right">{fmtPower(l.mean, 1)}</td>
                      <td className={`px-2 py-1 font-mono text-[11px] text-right font-semibold ${
                        l.loadingPct >= 90 ? 'text-danger' :
                        l.loadingPct >= 50 ? 'text-warn'   :
                        'text-success'
                      }`}>{l.loadingPct.toFixed(1)} %</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ── Line reactive power (AC PF only) ──────────────────── */}
      {acPfAvailable && lineReactiveStats && lineReactiveStats.length > 0 && (
        <section>
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">Lines · reactive power · AC PF ({lineReactiveStats.length})</h3>
            <button
              onClick={() => {
                if (!lineReactiveStats) return
                downloadCSV('results_line_reactive.csv',
                  ['name', 'min_MVAr', 'max_MVAr', 'mean_MVAr'],
                  lineReactiveStats.map(l => [l.name, l.min.toFixed(3), l.max.toFixed(3), l.mean.toFixed(3)]),
                )
                toast.success(`Exported ${lineReactiveStats.length} rows`)
              }}
              className="flex items-center gap-1 text-[11px] text-muted hover:text-accent"
            ><Download size={11} /> CSV</button>
          </div>
          <div className="border border-border rounded overflow-auto max-h-64">
            <table className="w-full text-xs border-collapse" style={{ minWidth: 480 }}>
              <thead className="sticky top-0 bg-bg-2 z-10">
                <tr className="border-b border-border">
                  <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Line</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Min q0 (MVAr)</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Max q0 (MVAr)</th>
                  <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Mean q0 (MVAr)</th>
                </tr>
              </thead>
              <tbody>
                {lineReactiveStats.map((l, i) => (
                  <tr key={l.name} className={i % 2 === 0 ? 'bg-bg' : 'bg-panel'}>
                    <td className="px-2 py-1 font-mono text-[11px]">{l.name}</td>
                    <td className="px-2 py-1 font-mono text-[11px] text-right">{l.min.toFixed(2)}</td>
                    <td className="px-2 py-1 font-mono text-[11px] text-right">{l.max.toFixed(2)}</td>
                    <td className="px-2 py-1 font-mono text-[11px] text-right">{l.mean.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* ── Top-loaded lines time-series chart ────────────────── */}
      <section>
        <div className="flex items-center mb-2">
          <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">Top-loaded lines (top {TOP_N_CHART})</h3>
          {topChart && topChart.cols.length > 0 && (
            <ChartActions
              className="ml-auto"
              chartRef={topLinesChartRef}
              onExportCSV={() => {
                const header = ['snapshot', ...topChart.cols]
                downloadCSV('results_top_lines.csv', header,
                  topChart.rows.map(r =>
                    [r.snapshot, ...topChart.cols.map(c => r[c] ?? '')]))
                toast.success('Exported')
              }}
              filename="results_top_lines" />
          )}
        </div>
        {!topChart || topChart.cols.length === 0 ? (
          <div className="border border-border rounded p-4 text-center text-xs text-muted">No data.</div>
        ) : (
          <div ref={topLinesChartRef}>
            {lfMode !== 'timeframe' ? (
              <SeasonalLineCardGrid
                mode={lfMode}
                rows={topChart.rows}
                cols={topChart.cols}
                colors={['#dc2626', '#0ea5e9', '#16a34a', '#d97706', '#7c3aed']}
                unit="MW"
                height={170}
              />
            ) : (
              <ResponsiveContainer width="100%" height={180}>
                <LineChart data={topChart.rows} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                  <CartesianGrid {...CHART_GRID} />
                  <XAxis dataKey="snapshot" tickFormatter={shortStamp} {...CHART_AXIS} />
                  <YAxis {...CHART_AXIS} label={yAxisLabel('MW')} />
                  <RTooltip {...CHART_TOOLTIP} labelFormatter={shortStamp} formatter={(v: number) => `${v.toFixed(2)} MW`} />
                  <Legend {...CHART_LEGEND} />
                  {topChart.cols.map((c, i) => (
                    <Line key={c} type="monotone" dataKey={c}
                          stroke={['#dc2626', '#0ea5e9', '#16a34a', '#d97706', '#7c3aed'][i % 5]}
                          strokeWidth={1.4} dot={false} />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        )}
      </section>

      {/* ── Transformers loading ──────────────────────────────── */}
      {/* Same table + chart structure as Lines, just over n.transformers_t.p0.
          Renders only when the network has transformers AND the LP produced a
          flow time series — keeps the page clean for pure single-voltage
          networks. */}
      {trafoStats && trafoStats.length > 0 && (
        <>
          <section>
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">Transformers ({trafoStats.length})</h3>
              <button onClick={exportTrafoStatsCSV}
                className="flex items-center gap-1 text-[11px] text-muted hover:text-accent"
              ><Download size={11} /> CSV</button>
            </div>
            <div className="border border-border rounded overflow-auto max-h-72">
              <table className="w-full text-xs border-collapse" style={{ minWidth: 720 }}>
                <thead className="sticky top-0 bg-bg-2 z-10">
                  <tr className="border-b border-border">
                    <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Name</th>
                    <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">HV bus</th>
                    <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">LV bus</th>
                    <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">s_nom</th>
                    <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Peak |p|</th>
                    <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Mean |p|</th>
                    <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Loading</th>
                  </tr>
                </thead>
                <tbody>
                  {trafoStats.map((t, i) => (
                    <tr key={t.name} className={i % 2 === 0 ? 'bg-bg' : 'bg-panel'}>
                      <td className="px-2 py-1 font-mono text-[11px]">{t.name}</td>
                      <td className="px-2 py-1 text-[11px]">{t.bus0}</td>
                      <td className="px-2 py-1 text-[11px]">{t.bus1}</td>
                      <td className="px-2 py-1 font-mono text-[11px] text-right">{fmtPower(t.s_nom, 1).replace('MW', 'MVA')}</td>
                      <td className="px-2 py-1 font-mono text-[11px] text-right">{fmtPower(t.peak, 1)}</td>
                      <td className="px-2 py-1 font-mono text-[11px] text-right">{fmtPower(t.mean, 1)}</td>
                      <td className={`px-2 py-1 font-mono text-[11px] text-right font-semibold ${
                        t.loadingPct >= 90 ? 'text-danger' :
                        t.loadingPct >= 50 ? 'text-warn'   :
                        'text-success'
                      }`}>{t.loadingPct.toFixed(1)} %</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          {/* Transformer reactive power (AC PF only) */}
          {acPfAvailable && trafoReactiveStats && trafoReactiveStats.length > 0 && (
            <section>
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">Transformers · reactive power · AC PF ({trafoReactiveStats.length})</h3>
                <button
                  onClick={() => {
                    if (!trafoReactiveStats) return
                    downloadCSV('results_transformer_reactive.csv',
                      ['name', 'min_MVAr', 'max_MVAr', 'mean_MVAr'],
                      trafoReactiveStats.map(t => [t.name, t.min.toFixed(3), t.max.toFixed(3), t.mean.toFixed(3)]),
                    )
                    toast.success(`Exported ${trafoReactiveStats.length} rows`)
                  }}
                  className="flex items-center gap-1 text-[11px] text-muted hover:text-accent"
                ><Download size={11} /> CSV</button>
              </div>
              <div className="border border-border rounded overflow-auto max-h-64">
                <table className="w-full text-xs border-collapse" style={{ minWidth: 480 }}>
                  <thead className="sticky top-0 bg-bg-2 z-10">
                    <tr className="border-b border-border">
                      <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Transformer</th>
                      <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Min q0 (MVAr)</th>
                      <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Max q0 (MVAr)</th>
                      <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Mean q0 (MVAr)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {trafoReactiveStats.map((t, i) => (
                      <tr key={t.name} className={i % 2 === 0 ? 'bg-bg' : 'bg-panel'}>
                        <td className="px-2 py-1 font-mono text-[11px]">{t.name}</td>
                        <td className="px-2 py-1 font-mono text-[11px] text-right">{t.min.toFixed(2)}</td>
                        <td className="px-2 py-1 font-mono text-[11px] text-right">{t.max.toFixed(2)}</td>
                        <td className="px-2 py-1 font-mono text-[11px] text-right">{t.mean.toFixed(2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {topTrafoChart && topTrafoChart.cols.length > 0 && (
            <section>
              <div className="flex items-center mb-2">
                <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">Top-loaded transformers (top {TOP_N_CHART})</h3>
                <ChartActions
                  className="ml-auto"
                  chartRef={topTrafoChartRef}
                  onExportCSV={() => {
                    const header = ['snapshot', ...topTrafoChart.cols]
                    downloadCSV('results_top_transformers.csv', header,
                      topTrafoChart.rows.map(r =>
                        [r.snapshot, ...topTrafoChart.cols.map(c => r[c] ?? '')]))
                    toast.success('Exported')
                  }}
                  filename="results_top_transformers" />
              </div>
              <div ref={topTrafoChartRef}>
                {lfMode !== 'timeframe' ? (
                  <SeasonalLineCardGrid
                    mode={lfMode}
                    rows={topTrafoChart.rows}
                    cols={topTrafoChart.cols}
                    colors={['#d97706', '#7c3aed', '#0ea5e9', '#16a34a', '#dc2626']}
                    unit="MW"
                    height={170}
                  />
                ) : (
                  <ResponsiveContainer width="100%" height={180}>
                    <LineChart data={topTrafoChart.rows} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                      <CartesianGrid {...CHART_GRID} />
                      <XAxis dataKey="snapshot" tickFormatter={shortStamp} {...CHART_AXIS} />
                      <YAxis {...CHART_AXIS} label={yAxisLabel('MW')} />
                      <RTooltip {...CHART_TOOLTIP} labelFormatter={shortStamp} formatter={(v: number) => `${v.toFixed(2)} MW`} />
                      <Legend {...CHART_LEGEND} />
                      {topTrafoChart.cols.map((c, i) => (
                        <Line key={c} type="monotone" dataKey={c}
                              stroke={['#d97706', '#7c3aed', '#0ea5e9', '#16a34a', '#dc2626'][i % 5]}
                              strokeWidth={1.4} dot={false} />
                      ))}
                    </LineChart>
                  </ResponsiveContainer>
                )}
              </div>
            </section>
          )}
        </>
      )}

      {/* Bus marginal prices, per-bus tables, hourly price chart, price duration
          curve, and price drivers all live in the dedicated "Prices" tab now.
          Removing the duplicate sections from LoadFlow keeps this tab focused
          on the physical-network view (flows, voltages, losses). */}

      {/* Marginal price chart + duration curve moved to the dedicated
          "Prices" tab. Disabled via SHOW_RETIRED_SECTIONS (see top of file);
          the `priceChart` useMemo stays in the tree regardless. */}
      {SHOW_RETIRED_SECTIONS && priceChart && (
        <section>
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">
              Hourly marginal price
              {priceView === 'per_bus' && priceChart.busTotal > priceChart.cols.length && (
                <span className="ml-2 text-[10px] font-normal text-muted">
                  (top {priceChart.cols.length} of {priceChart.busTotal} buses by mean)
                </span>
              )}
            </h3>
            <div className="flex items-center gap-2">
              <div className="inline-flex rounded border border-border overflow-hidden text-[10px]"
                title="LP dual = raw shadow price of the power-balance constraint (PyPSA n.buses_t.marginal_price). Merit order = LP dual + curtailment_cost subsidy added back at hours where a renewable was the marginal dispatch unit. Use Merit order to see the physical merit-order price without the curtailment-cost dispatch incentive.">
                <button onClick={() => setPriceSource('lp')}
                  className={`px-2 py-0.5 transition-colors ${priceSource === 'lp' ? 'bg-accent text-white' : 'bg-bg text-muted hover:text-accent'}`}>
                  LP dual
                </button>
                <button onClick={() => setPriceSource('merit')}
                  disabled={!priceChart.hasAdjusted}
                  className={`px-2 py-0.5 border-l border-border transition-colors disabled:opacity-50 ${priceSource === 'merit' ? 'bg-accent text-white' : 'bg-bg text-muted hover:text-accent'}`}>
                  Merit order
                </button>
              </div>
              <div className="inline-flex rounded border border-border overflow-hidden text-[10px]">
                <button onClick={() => setPriceView('avg')}
                  className={`px-2 py-0.5 transition-colors ${priceView === 'avg' ? 'bg-accent text-white' : 'bg-bg text-muted hover:text-accent'}`}>
                  Average
                </button>
                <button onClick={() => setPriceView('per_bus')}
                  className={`px-2 py-0.5 border-l border-border transition-colors ${priceView === 'per_bus' ? 'bg-accent text-white' : 'bg-bg text-muted hover:text-accent'}`}>
                  Per bus
                </button>
              </div>
            </div>
          </div>
          {/* Hint when negative-price hours exist on the LP-dual series.
              Tells the user the toggle exists; the merit-order series almost
              always lifts those hours back to 0 or positive. */}
          {priceSource === 'lp' && priceChart.negativeHours > 0 && (
            <div className="mb-2 text-[10px] text-muted italic">
              {priceChart.negativeHours} snapshot{priceChart.negativeHours === 1 ? '' : 's'} contain at least one
              negative-price bus. This is the LP shadow price; setting <code>curtailment_cost</code> on renewables
              subsidises their dispatch by that amount, which the LP dual reflects as a negative price when the
              renewable is marginal. Switch to <span className="font-medium">Merit order</span> to see prices
              without that subsidy.
            </div>
          )}
          <ResponsiveContainer width="100%" height={200}>
            <LineChart
              data={priceView === 'avg' ? priceChart.avgRows : priceChart.perBusRows}
              margin={{ top: 4, right: 4, left: 0, bottom: 0 }}
            >
              <CartesianGrid {...CHART_GRID} />
              <XAxis dataKey="snapshot" tickFormatter={shortStamp} {...CHART_AXIS} />
              <YAxis {...CHART_AXIS} label={yAxisLabel('€/MWh')} />
              <RTooltip {...CHART_TOOLTIP} labelFormatter={shortStamp} formatter={(v: number) => `${v.toFixed(2)} €/MWh`} />
              {priceView === 'per_bus' && priceChart.cols.length <= 8 && (
                <Legend {...CHART_LEGEND} />
              )}
              {priceView === 'avg' ? (
                <Line type="monotone" dataKey="avg" name="Average"
                      stroke="#0ea5e9" strokeWidth={1.8} dot={false} />
              ) : (
                priceChart.cols.map((c, i) => (
                  <Line key={c} type="monotone" dataKey={c} name={c}
                        stroke={[
                          '#dc2626', '#0ea5e9', '#16a34a', '#d97706', '#7c3aed',
                          '#db2777', '#0891b2', '#65a30d', '#c2410c', '#6d28d9',
                          '#e11d48', '#1d4ed8',
                        ][i % 12]}
                        strokeWidth={1.2} dot={false} />
                ))
              )}
            </LineChart>
          </ResponsiveContainer>
        </section>
      )}

      {/* Price drivers table moved to the "Prices" tab. Disabled via
          SHOW_RETIRED_SECTIONS (see top of file). */}
      {SHOW_RETIRED_SECTIONS && priceTS && (
        <section>
          <div className="flex items-center gap-2 mb-2">
            <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">Price drivers</h3>
            <label className="text-[10px] text-muted ml-2">|price| ≥</label>
            <input type="number" step="100" min={0}
              value={driverThreshold}
              onChange={e => setDriverThreshold(Math.max(0, Number(e.target.value) || 0))}
              className="bg-bg border border-border rounded px-1.5 py-0.5 text-[10px] font-mono w-20" />
            <span className="text-[10px] text-muted">€/MWh</span>
            {priceDrivers && (
              <span className="text-[10px] text-muted ml-auto">
                {priceDrivers.total_above_threshold} cell{priceDrivers.total_above_threshold === 1 ? '' : 's'}
                {priceDrivers.truncated && ` (showing top ${priceDrivers.rows.length})`}
              </span>
            )}
          </div>
          <p className="text-[11px] text-muted mb-2">
            For every (bus, snapshot) above the threshold, the table shows the
            most-likely marginal generator and a tag pointing at the cause:
            <span className="ml-1 px-1 rounded bg-danger/10 text-danger font-mono text-[9px]">load_shedding</span>
            <span className="ml-1 px-1 rounded bg-warn/10 text-warn font-mono text-[9px]">thermal_peaker</span>
            <span className="ml-1 px-1 rounded bg-accent/10 text-accent font-mono text-[9px]">transmission</span>
            <span className="ml-1 px-1 rounded bg-muted/10 text-muted font-mono text-[9px]">unattributed</span>.
          </p>
          {!priceDrivers || priceDrivers.rows.length === 0 ? (
            <div className="border border-border rounded p-3 text-center text-[11px] text-muted">
              No cells above {driverThreshold} €/MWh.
            </div>
          ) : (
            <div className="border border-border rounded overflow-auto max-h-80">
              <table className="w-full text-xs border-collapse" style={{ minWidth: 720 }}>
                <thead className="sticky top-0 bg-bg-2 z-10">
                  <tr className="border-b border-border">
                    <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Snapshot</th>
                    <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Bus</th>
                    <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Price (€/MWh)</th>
                    <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Marginal gen</th>
                    <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Carrier</th>
                    <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Gen MC</th>
                    <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Dispatch</th>
                    <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Diagnosis</th>
                  </tr>
                </thead>
                <tbody>
                  {priceDrivers.rows.map((r, i) => (
                    <tr key={i} className={i % 2 === 0 ? 'bg-bg' : 'bg-panel'}>
                      <td className="px-2 py-1 font-mono text-[10px]">{shortStamp(r.snapshot)}</td>
                      <td className="px-2 py-1 font-mono text-[11px]">{r.bus}</td>
                      <td className={`px-2 py-1 font-mono text-[11px] text-right font-semibold ${
                        r.price < 0 ? 'text-accent' : r.price > 1000 ? 'text-danger' : 'text-text'
                      }`}>{r.price.toFixed(2)}</td>
                      <td className="px-2 py-1 font-mono text-[10px] truncate" style={{ maxWidth: 180 }}
                        title={r.marginal_gen ?? ''}>
                        {r.marginal_gen ?? '—'}
                      </td>
                      <td className="px-2 py-1 text-[10px] text-muted">{r.carrier || '—'}</td>
                      <td className="px-2 py-1 font-mono text-[10px] text-right">{r.marginal_cost.toFixed(2)}</td>
                      <td className="px-2 py-1 font-mono text-[10px] text-right">{r.dispatch.toFixed(1)}</td>
                      <td className="px-2 py-1">
                        <span className={`px-1.5 py-0.5 rounded text-[9px] font-mono ${
                          r.diagnosis === 'load_shedding' ? 'bg-danger/10 text-danger' :
                          r.diagnosis === 'thermal_peaker' ? 'bg-warn/10 text-warn' :
                          r.diagnosis === 'transmission'   ? 'bg-accent/10 text-accent' :
                                                              'bg-muted/10 text-muted'
                        }`}>{r.diagnosis}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </div>
  )
}
