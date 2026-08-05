import { useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip as RTooltip, Legend,
} from 'recharts'
import { resultsApi } from '../../api/simulation'
import { networkApi } from '../../api/network'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import type { Generator, Load, StorageUnit } from '../../api/types'
import { Download } from 'lucide-react'
import toast from 'react-hot-toast'
import {
  type TSPayload, type WeightCtx,
  perPeriodWeightedSum, perPeriodWeightedSumSplit, perPeriodPerColumnWeightedSum,
  weightedSum, weightedSumSplit, averageScaling, hasScaling, generatorGroup,
  isRenewableCarrier, fmtEnergy, fmtCurrency, downloadCSV, KPI, ChartActions,
  ChartCard, colourForCarrier, CHART_GRID, CHART_AXIS, CHART_LEGEND, CHART_TOOLTIP,
} from './shared'
import { storageCarrierKey, loadCarrierKey } from './carrierAliases'

// ── Aggregated overview ────────────────────────────────────────────────────
// Shown when the user picks the "Aggregated" period sub-tab. Replaces the
// per-period detail charts with system-level bar charts where each bar is
// one period:
//   • Economics   — system cost breakdown (CAPEX / OPEX / curtailment / VOLL)
//   • Generation  — energy mix by carrier per period (abs MWh ↔ % of total)
//   • Storage     — charged / discharged per period
//   • Renewables  — curtailment per period
//   • Load        — by carrier per period (electricity / heat / H2 / ...)
//
// Built on the perPeriod{WeightedSum,WeightedSumSplit,PerColumnWeightedSum}
// helpers from shared.tsx — the weight context comes from snapshot_weightings
// × investment_period_weightings.years, matching what n.statistics() reports.

// Carrier → colour comes from the shared `colourForCarrier` (see shared.tsx) so
// the per-period bar charts here match the per-snapshot dispatch stack exactly.

interface AggregatedOverviewProps {
  weightCtx: WeightCtx
}

/**
 * True when any payload is a WINDOW rather than the whole series.
 *
 * This tab reports whole-horizon energy and cost: it computes over `fullRange`
 * — the entire payload, whatever it holds — at 13 sites, deliberately ignoring
 * the Horizon filter, because summarising the horizon is its job. Hand it a
 * windowed payload and every KPI silently becomes a window total under a
 * horizon label.
 *
 * Unreachable today: this tab's getters are called with zero arguments, so the
 * backend emits no `range` key at all. It exists for the day someone converts
 * them, which is the most likely way this design gets broken later.
 */
export function isPartialPayload(
  payloads: Array<TSPayload | null | undefined>,
): boolean {
  return payloads.some(p => p?.range != null && !p.range.complete)
}

export default function AggregatedOverview({ weightCtx }: AggregatedOverviewProps) {
  const currentProject = useUIStore(s => s.currentProject)
  // Refs for SVG export — one per chart section. Recharts emits a single
  // <svg> inside the ResponsiveContainer parent, so the ref on the wrapping
  // div is enough for the export helper to find it.
  const economicsRef   = useRef<HTMLDivElement | null>(null)
  const generationRef  = useRef<HTMLDivElement | null>(null)
  const storageRef     = useRef<HTMLDivElement | null>(null)
  const curtailmentRef = useRef<HTMLDivElement | null>(null)
  const loadRef        = useRef<HTMLDivElement | null>(null)

  const { data: gensTS }      = useQuery({ queryKey: nk(currentProject, 'results', 'generators'),       queryFn: () => resultsApi.getGeneratorResults() })
  const { data: storPowerTS } = useQuery({ queryKey: nk(currentProject, 'results', 'storage_dispatch'), queryFn: () => resultsApi.getStorageDispatchResults() })
  const { data: loadTS }      = useQuery({ queryKey: nk(currentProject, 'results', 'loads'),            queryFn: () => resultsApi.getLoadResults() })
  // Wrapped (not bare) because `getCurtailment`/`getLostLoad` now take an
  // optional `range` — calling with no arguments here is still
  // byte-identical to the old bare-reference request, and this tab must
  // stay on whole-horizon payloads (never pass a window).
  const { data: curtailTS }   = useQuery({ queryKey: nk(currentProject, 'results', 'curtailment'),      queryFn: () => resultsApi.getCurtailment() })
  const { data: lostLoad }    = useQuery({ queryKey: nk(currentProject, 'results', 'lost_load'),        queryFn: () => resultsApi.getLostLoad() })
  const { data: cost }        = useQuery({ queryKey: nk(currentProject, 'results', 'cost_breakdown'),   queryFn: resultsApi.getCostBreakdown })

  const { data: generators = [] }   = useQuery({ queryKey: nk(currentProject, 'generators'),    queryFn: networkApi.getGenerators })
  const { data: storageUnits = [] } = useQuery({ queryKey: nk(currentProject, 'storage_units'), queryFn: networkApi.getStorageUnits })
  const { data: loads = [] }        = useQuery({ queryKey: nk(currentProject, 'loads'),         queryFn: networkApi.getLoads })

  // ── Whole-horizon KPIs ─────────────────────────────────────────────────
  // Same KPI strip the per-period Dispatch tab renders, but here it spans
  // every period (range = full TS). Users see the annualised system totals
  // at a glance before drilling into the period bar charts below.
  const refTs = (gensTS as TSPayload | null)
              ?? (loadTS as TSPayload | null)
              ?? (storPowerTS as TSPayload | null)
  const fullRange = useMemo(() => {
    const n = refTs?.data.length ?? 0
    return { from: 0, to: Math.max(0, n - 1) }
  }, [refTs])

  const scaled = hasScaling(weightCtx)
  const avgScale = useMemo(
    () => averageScaling(weightCtx, refTs?.index ?? [], fullRange, 'generators'),
    [weightCtx, refTs, fullRange],
  )
  const scaleHint = scaled
    ? ` (× ${avgScale.toFixed(2)} avg weighting applied — see snapshot_weightings × investment_period_weightings.years)`
    : ''

  // Per-carrier-group buckets — mirror Dispatch.tsx's groupCols.
  const groupCols = useMemo(() => {
    const thermal = new Set<string>()
    const renew = new Set<string>()
    for (const g of generators as Generator[]) {
      if (generatorGroup(g.carrier) === 'Thermal') thermal.add(g.name)
      else renew.add(g.name)
    }
    return { thermal, renew }
  }, [generators])
  const storageNames = useMemo(() => new Set((storageUnits as StorageUnit[]).map(s => s.name)), [storageUnits])
  const loadNames    = useMemo(() => new Set((loads as Load[]).map(l => l.name)), [loads])

  const kpis = useMemo(() => {
    const thermal   = weightedSum(gensTS as TSPayload | null, groupCols.thermal, weightCtx, fullRange, 'generators')
    const renewable = weightedSum(gensTS as TSPayload | null, groupCols.renew,   weightCtx, fullRange, 'generators') ?? 0
    const load      = weightedSum(loadTS as TSPayload | null, loadNames,         weightCtx, fullRange, 'generators')
    const storageSplit = weightedSumSplit(
      storPowerTS as TSPayload | null, storageNames, weightCtx, fullRange, 'generators',
    )
    const curtailEnergy = weightedSum(
      curtailTS as TSPayload | null, groupCols.renew, weightCtx, fullRange, 'generators',
    )
    const potential = renewable + (curtailEnergy ?? 0)
    const curtailPct = curtailEnergy != null && potential > 0
      ? (curtailEnergy / potential) * 100
      : null
    return {
      thermal,
      renewable,
      load,
      totalGen: (thermal ?? 0) + renewable,
      curtailEnergy,
      curtailPct,
      storageDischarge: storageSplit.positive,
      storageCharge:    storageSplit.negative,
    }
  }, [gensTS, loadTS, storPowerTS, curtailTS, groupCols, loadNames, storageNames,
      weightCtx, fullRange])

  // Lost load totals (whole horizon, weighted). Mirrors Dispatch.tsx.
  const lostLoadTotals = useMemo(() => {
    if (!lostLoad) return null
    const ts = lostLoad as TSPayload | null | undefined
    if (!ts || ts.data.length === 0) {
      const ll = lostLoad as unknown as { total_mwh?: number; total_cost_eur?: number }
      return ll ? { mwh: ll.total_mwh ?? 0, cost: ll.total_cost_eur ?? 0 } : null
    }
    const cols = new Set(ts.columns)
    const split = weightedSumSplit(ts, cols, weightCtx, fullRange, 'generators')
    const mwh = split.positive ?? 0
    const llMeta = lostLoad as unknown as { total_mwh?: number; total_cost_eur?: number }
    const voll = (llMeta.total_mwh && llMeta.total_mwh > 0)
      ? ((llMeta.total_cost_eur ?? 0) / llMeta.total_mwh) : 0
    return { mwh, cost: mwh * voll }
  }, [lostLoad, weightCtx, fullRange])

  // Curtailment cost (whole horizon, weighted by objective column).
  const curtailmentCost = useMemo(() => {
    const ts = curtailTS as TSPayload | null
    if (!ts || ts.data.length === 0) return 0
    const costByName = new Map<string, number>()
    for (const g of generators as Generator[]) {
      const c = (g as unknown as { curtailment_cost?: number }).curtailment_cost
      if (c && Number.isFinite(c) && c > 0) costByName.set(g.name, c)
    }
    if (costByName.size === 0) return 0
    let total = 0
    for (const [col, costCoef] of costByName) {
      const summed = weightedSum(ts, new Set([col]), weightCtx, fullRange, 'objective')
      if (summed != null) total += costCoef * summed
    }
    return total
  }, [curtailTS, generators, weightCtx, fullRange])

  // ── Generation by carrier per period ───────────────────────────────────
  // Group generator columns by carrier, sum each carrier per period.
  const generationMix = useMemo(() => {
    const ts = gensTS as TSPayload | null
    if (!ts) return null
    // Map each column to its carrier-bucket. Match each generator's stored
    // carrier; fall back to "other".
    const carrierByGen = new Map<string, string>()
    for (const g of generators as Generator[]) carrierByGen.set(g.name, g.carrier ?? 'other')
    // Build a synthetic TSPayload where columns are CARRIERS (not generators)
    // — sum each carrier's column values per snapshot — then run the per-period
    // helper. This re-uses the existing weighting path without specialising it.
    const carriers = [...new Set(ts.columns.map(c => carrierByGen.get(c) ?? 'other'))].sort()
    const data: number[][] = []
    for (let row = 0; row < ts.data.length; row++) {
      const out = new Array(carriers.length).fill(0)
      ts.columns.forEach((col, ci) => {
        const v = ts.data[row][ci]
        if (!Number.isFinite(v)) return
        const c = carrierByGen.get(col) ?? 'other'
        const idx = carriers.indexOf(c)
        out[idx] += v
      })
      data.push(out)
    }
    const synth: TSPayload = { index: ts.index, columns: carriers, data, periods: ts.periods }
    const rows = perPeriodPerColumnWeightedSum(synth, new Set(carriers), weightCtx, 'generators')
    return { rows, carriers }
  }, [gensTS, generators, weightCtx])

  // Generation toggle: absolute MWh / percent of total
  const [genMode, setGenMode] = useState<'abs' | 'pct'>('abs')
  const generationRows = useMemo(() => {
    if (!generationMix) return null
    if (genMode === 'abs') return generationMix.rows
    // Normalise each row so its carriers sum to 100.
    return generationMix.rows.map(r => {
      const total = generationMix.carriers.reduce((a, c) => a + (Number(r[c]) || 0), 0)
      const out: Record<string, number | string | null> = { period: r.period }
      for (const c of generationMix.carriers) {
        const v = Number(r[c]) || 0
        out[c] = total > 0 ? (v / total) * 100 : 0
      }
      return out as typeof r
    })
  }, [generationMix, genMode])

  // ── Storage charged / discharged per period (per carrier) ──────────────
  // Splits each period's storage flow into one band per storage carrier
  // (battery / hydrogen / heat / …) so a network with multiple storage
  // technologies doesn't conflate them into a single bar. Discharge bars
  // stack into one positive group, charge bars into a separate negative
  // group (rendered as positive bars in a side-by-side category — see
  // the JSX below). Falls back to a single "storage" bucket when every
  // storage unit shares the same carrier.
  const storageBars = useMemo(() => {
    const ts = storPowerTS as TSPayload | null
    const sus = storageUnits as StorageUnit[]
    if (!ts || sus.length === 0) return null
    // Map column → carrier via the SHARED alias normaliser so the chart's
    // bars match what Dispatch.tsx, CapacityExpansion.tsx, and Economics.tsx
    // show for the same logical carrier. Without this, a project with both
    // `BESS` and `battery` storage units would fragment into two bars here
    // while collapsing into one "Battery" group on the other tabs —
    // breaking the cross-tab consistency contract documented in carrierAliases.ts.
    const carrierByName = new Map<string, string>()
    for (const s of sus) carrierByName.set(s.name, storageCarrierKey(s.carrier))
    const carriers = [...new Set([...carrierByName.values()])].sort()
    // Build two synthetic TS frames — one for GROSS discharge (positive
    // storage flows summed into the carrier column, negatives masked to 0)
    // and one for GROSS charge (negative flows absoluted into the carrier
    // column, positives masked to 0). Masking happens per-generator BEFORE
    // the carrier sum so that two storage units sharing a carrier with
    // opposite simultaneous flows (e.g. unit-A discharges 10, unit-B
    // charges -3) contribute 10 to discharge AND 3 to charge — the gross
    // values the user expects from the chart caption.
    const synthCols = carriers
    const posData: number[][] = []
    const negData: number[][] = []
    for (let row = 0; row < ts.data.length; row++) {
      const pos = new Array(synthCols.length).fill(0)
      const neg = new Array(synthCols.length).fill(0)
      ts.columns.forEach((col, ci) => {
        const v = ts.data[row][ci]
        if (!Number.isFinite(v)) return
        const carrier = carrierByName.get(col) ?? 'storage'
        const idx = synthCols.indexOf(carrier)
        if (idx < 0) return
        if (v > 0) pos[idx] += v
        else if (v < 0) neg[idx] += -v
      })
      posData.push(pos)
      negData.push(neg)
    }
    const posTs: TSPayload = { index: ts.index, columns: synthCols, data: posData, periods: ts.periods }
    const negTs: TSPayload = { index: ts.index, columns: synthCols, data: negData, periods: ts.periods }
    const posRows = perPeriodPerColumnWeightedSum(posTs, new Set(synthCols), weightCtx, 'generators')
    const negRows = perPeriodPerColumnWeightedSum(negTs, new Set(synthCols), weightCtx, 'generators')
    // Merge into one row per period with `<carrier>_d` (discharge) and
    // `<carrier>_c` (charge) keys so the BarChart can render two stacks.
    const byPeriod = new Map<number | string | null, Record<string, number | string | null>>()
    for (const r of posRows) {
      const entry = byPeriod.get(r.period) ?? { period: r.period }
      for (const c of synthCols) entry[`${c}_d`] = Number(r[c] ?? 0)
      byPeriod.set(r.period, entry)
    }
    for (const r of negRows) {
      const entry = byPeriod.get(r.period) ?? { period: r.period }
      for (const c of synthCols) entry[`${c}_c`] = Number(r[c] ?? 0)
      byPeriod.set(r.period, entry)
    }
    const rows = [...byPeriod.values()].sort((a, b) => {
      const ap = a.period, bp = b.period
      if (ap == null) return -1
      if (bp == null) return 1
      if (typeof ap === 'number' && typeof bp === 'number') return ap - bp
      return String(ap).localeCompare(String(bp))
    })
    return { rows, carriers: synthCols }
  }, [storPowerTS, storageUnits, weightCtx])

  // ── Renewables curtailment per period (per carrier) ───────────────────
  // Splits each period's renewable curtailment into one stacked band per
  // carrier (solar / onwind / offwind / …) so the chart shows which
  // technology is being spilled, not just the total. Falls back to one
  // 'renewables' bucket when every renewable generator shares a carrier.
  const curtailmentBars = useMemo(() => {
    const ts = curtailTS as TSPayload | null
    if (!ts) return null
    const renewables = (generators as Generator[]).filter(g => isRenewableCarrier(g.carrier ?? ''))
    if (renewables.length === 0) return null
    const carrierByName = new Map<string, string>()
    for (const g of renewables) carrierByName.set(g.name, (g.carrier ?? '').trim() || 'renewables')
    const carriers = [...new Set([...carrierByName.values()])].sort()
    // Build a synthetic TS where columns are carriers (Σ over generators
    // sharing that carrier), then aggregate per period.
    const synthData: number[][] = []
    for (let row = 0; row < ts.data.length; row++) {
      const out = new Array(carriers.length).fill(0)
      ts.columns.forEach((col, ci) => {
        const v = ts.data[row][ci]
        if (!Number.isFinite(v)) return
        const carrier = carrierByName.get(col)
        if (!carrier) return  // non-renewable column → ignore
        const idx = carriers.indexOf(carrier)
        if (idx >= 0) out[idx] += v
      })
      synthData.push(out)
    }
    const synth: TSPayload = { index: ts.index, columns: carriers, data: synthData, periods: ts.periods }
    const rows = perPeriodPerColumnWeightedSum(synth, new Set(carriers), weightCtx, 'generators')
    return { rows, carriers }
  }, [curtailTS, generators, weightCtx])

  // ── Load per period by carrier ────────────────────────────────────────
  const loadByCarrier = useMemo(() => {
    const ts = loadTS as TSPayload | null
    if (!ts) return null
    // Canonicalise load carriers through the shared alias so `''` /
    // `'AC'` / `'electricity'` / `'electrical'` all collapse into one
    // 'electrical' bar — matching the per-period dispatch tab grouping
    // and the cross-tab filter chips. Without this, a project that
    // mixes naming conventions would fragment into multiple electrical
    // bars on this chart.
    const carrierByLoad = new Map<string, string>()
    for (const l of loads as Load[]) carrierByLoad.set(l.name, loadCarrierKey(l.carrier))
    const carriers = [...new Set(ts.columns.map(c => carrierByLoad.get(c) ?? 'electrical'))].sort()
    const data: number[][] = []
    for (let row = 0; row < ts.data.length; row++) {
      const out = new Array(carriers.length).fill(0)
      ts.columns.forEach((col, ci) => {
        const v = ts.data[row][ci]
        if (!Number.isFinite(v)) return
        const c = carrierByLoad.get(col) ?? 'electrical'
        out[carriers.indexOf(c)] += v
      })
      data.push(out)
    }
    const synth: TSPayload = { index: ts.index, columns: carriers, data, periods: ts.periods }
    const rows = perPeriodPerColumnWeightedSum(synth, new Set(carriers), weightCtx, 'generators')
    return { rows, carriers }
  }, [loadTS, loads, weightCtx])

  // ── Economics: cost per period breakdown ───────────────────────────────
  // Each bar's CAPEX/OPEX comes from cost.by_period (backend already applied
  // investment_period_weightings.years to each period). The OPEX bar bundles
  // dispatch OPEX + curtailment penalty so the bar height matches the
  // "Total OPEX" KPI shown in the Dispatch tab's per-period view —
  // otherwise the user sees an OPEX bar that doesn't add up to the dispatch
  // cost for that period. VOLL stays a separate stack so reliability cost
  // is visually distinguishable.
  const economicsBars = useMemo(() => {
    if (!cost) return null
    // VOLL per period: positive side of lostLoad TS is MWh shed (extensive).
    // To plot on a € axis, multiply by the per-MWh VOLL price recovered from
    // the lost_load payload's total_cost_eur / total_mwh ratio. Without this
    // multiplier the chart shows €100 when the asset's real shed-load cost
    // is €100 × VOLL = €10M+ — off by ~5 orders of magnitude on typical
    // VOLL = 10,000-100,000 €/MWh.
    const llTs = lostLoad as TSPayload | null | undefined
    const llMeta = lostLoad as unknown as {
      total_mwh?: number; total_cost_eur?: number; voll_eur_per_mwh?: number
    }
    // Prefer backend-provided voll_eur_per_mwh. Fall back to ratio for
    // pre-fix payloads / stale caches.
    const voll = llMeta?.voll_eur_per_mwh
      ?? ((llMeta?.total_mwh && llMeta.total_mwh > 0)
        ? ((llMeta.total_cost_eur ?? 0) / llMeta.total_mwh) : 0)
    const vollPerPeriod = llTs
      ? perPeriodWeightedSumSplit(llTs, new Set(llTs.columns), weightCtx, 'generators')
          .map(r => r.positive * voll)
      : []
    // Curtailment penalty per period: only generators with curtailment_cost > 0.
    const curtTs = curtailTS as TSPayload | null
    const renewCostByName = new Map<string, number>()
    for (const g of generators as Generator[]) {
      const c = (g as unknown as { curtailment_cost?: number }).curtailment_cost
      if (c && Number.isFinite(c) && c > 0) renewCostByName.set(g.name, c)
    }
    const curtPenaltyPerPeriod: number[] = []
    if (curtTs && renewCostByName.size > 0) {
      const cols = [...renewCostByName.keys()]
      for (const col of cols) {
        const sums = perPeriodWeightedSum(curtTs, new Set([col]), weightCtx, 'objective')
        sums.forEach((s, i) => {
          curtPenaltyPerPeriod[i] = (curtPenaltyPerPeriod[i] ?? 0) + s.value * (renewCostByName.get(col) ?? 0)
        })
      }
    }
    // Period axis: prefer cost.by_period (authoritative — it's what the
    // CAPEX/OPEX values are keyed by). Fall back to any /results/* TS
    // payload's periods array when by_period is empty (single-period
    // network) so the per-period TS sums (VOLL, curtailment) still align.
    const byPeriodMap = new Map<number | string, { capex: number; opex: number }>()
    for (const p of cost.by_period ?? []) {
      const key = typeof p.period === 'string' ? p.period : Number(p.period)
      byPeriodMap.set(key, { capex: p.capex, opex: p.opex })
    }
    const axisFromBp = (cost.by_period ?? []).map(p =>
      typeof p.period === 'string' ? p.period : Number(p.period))
    const axisFromTs = (gensTS as TSPayload | null)?.periods
                    ?? (loadTS as TSPayload | null)?.periods
                    ?? llTs?.periods
    const periodAxis: Array<number | string> = axisFromBp.length > 0
      ? axisFromBp
      : axisFromTs
        ? (() => {
            const uniq = [...new Set(axisFromTs)]
            const allNumeric = uniq.every(p => typeof p === 'number')
            return allNumeric
              ? (uniq as number[]).sort((a, b) => a - b)
              : uniq.map(String).sort()
          })()
        : ['all']
    // Even-distribution fallback only when by_period is empty (single-period
    // or pre-multi-period network). Otherwise use the per-period values.
    const evenCapex = (cost.capex ?? 0) / Math.max(periodAxis.length, 1)
    const evenOpex  = (cost.opex  ?? 0) / Math.max(periodAxis.length, 1)
    return periodAxis.map((p, i) => {
      const bp = byPeriodMap.get(p)
      const capex = bp ? bp.capex : evenCapex
      const opex  = bp ? bp.opex  : evenOpex
      const curt  = curtPenaltyPerPeriod[i] ?? 0
      const voll  = vollPerPeriod[i] ?? 0
      return {
        period: p,
        CAPEX: capex,
        // Bundle dispatch OPEX + curtailment penalty so the OPEX bar
        // matches the "Total OPEX" KPI in the per-period Dispatch view.
        OPEX: opex + curt,
        VOLL: voll,
      }
    })
  }, [cost, lostLoad, curtailTS, generators, gensTS, loadTS, weightCtx])

  if (!gensTS && !loadTS && !cost) {
    return (
      <div className="p-6 text-center text-xs text-muted">
        No solved network. Run a simulation (header → <span className="font-medium text-text">Run LOPF</span>) to populate aggregated results.
      </div>
    )
  }

  // Must come AFTER the loading/empty-state guard above so a tab that is
  // still loading is never mistaken for one that received a partial
  // payload — see isPartialPayload's doc comment for why this matters.
  //
  // curtailTS / lostLoad are listed alongside gensTS/loadTS/storPowerTS
  // deliberately, not as an afterthought: they feed `fullRange` sums too
  // (curtailTS at the curtailment-energy/-cost KPIs and per-period bars;
  // lostLoad at the lost-load KPIs and per-period VOLL bars), and they are
  // exactly the two getters (`getCurtailment` / `getLostLoad`) this plan
  // widened to accept an optional range — the most likely pair to be
  // converted to a ranged call next. Omitting them would let a future
  // "just these two" conversion pass this guard silently.
  const partial = isPartialPayload([
    gensTS as TSPayload | null, loadTS as TSPayload | null, storPowerTS as TSPayload | null,
    curtailTS as TSPayload | null, lostLoad as TSPayload | null,
  ])
  if (partial) {
    return (
      <div className="p-4 text-sm text-warn">
        This tab reports whole-horizon totals, but the results it received cover
        only part of the horizon. Totals are unavailable — reload the Results
        panel, and if this persists it is a bug rather than a setting.
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-5 p-5 overflow-y-auto h-full text-sm [&>*]:shrink-0">

      <div className="text-[11px] text-muted -mt-2">
        Aggregated view spans every investment period. KPIs use snapshot
        weightings × period years, matching PyPSA's <code>n.statistics()</code>.
        Pick a specific period from the strip above to drill into hourly detail.
      </div>

      {/* ── System dispatch KPI strip (whole horizon, weighted) ── */}
      {/* Mirrors the per-period Dispatch tab's KPI strip but spans every
          period. Lets the user read off annualised totals before drilling
          into the bar charts below. */}
      <section>
        <h3 className="text-[12.5px] font-semibold text-text mb-2.5 flex items-center gap-2 tracking-[-0.005em]">
          System dispatch
          {scaled && (
            <span className="text-[10px] font-normal text-accent bg-accent/10 px-1.5 py-0.5 rounded font-mono"
              title={`Energy totals scaled by snapshot_weightings.generators × investment_period_weightings.years. Average factor across the full horizon: ${avgScale.toFixed(2)}.`}>
              × {avgScale.toFixed(2)} weighted
            </span>
          )}
        </h3>
        <div className="grid grid-cols-3 gap-3">
          <KPI accent label="Total demand" value={fmtEnergy(kpis.load)}     hint={'Σ p_set × snapshot_weightings × period years' + scaleHint} />
          <KPI label="Thermal"          value={fmtEnergy(kpis.thermal)}  hint={'Σ p_t × weighting over thermal generators' + scaleHint} />
          <KPI label="Renewables"       value={fmtEnergy(kpis.renewable)} hint={'Σ p_t × weighting over renewable generators' + scaleHint} />
          <KPI label="Total generation" value={fmtEnergy(kpis.totalGen)} hint={'Thermal + Renewables (weighted)' + scaleHint} />
          <KPI label="Storage discharged"
               value={fmtEnergy(kpis.storageDischarge)}
               hint={'energy released by storage units (production)' + scaleHint} />
          <KPI label="Storage charged"
               value={fmtEnergy(kpis.storageCharge)}
               hint={'energy absorbed by storage units (consumption)' + scaleHint} />
          <KPI label="Renewable curtailment"
               value={kpis.curtailEnergy != null
                 ? `${fmtEnergy(kpis.curtailEnergy)}${kpis.curtailPct != null ? ` · ${kpis.curtailPct.toFixed(1)} %` : ''}`
                 : '—'}
               hint={'Σ max(p_max_pu × p_nom − p, 0) over renewable generators' + scaleHint} />
          <KPI label="Lost load"
               value={lostLoadTotals ? fmtEnergy(lostLoadTotals.mwh) : '—'}
               hint={"Demand the LP couldn't serve — absorbed by VOLL slack generators per bus. Set Solver Settings → VOLL > 0 to enable." + scaleHint} />
          <KPI label="Lost-load cost"
               value={lostLoadTotals ? fmtCurrency(lostLoadTotals.cost) : '—'}
               hint={'MWh of lost load × the VOLL set in Solver Settings (e.g. 3000–10000 €/MWh).' + scaleHint} />
        </div>
      </section>

      {/* ── Operational expenditure (OPEX) — whole-horizon totals ── */}
      {/* Total OPEX + per-component breakdown. Same data the per-period
          tab uses, just unsliced. PyPSA's n.statistics() OPEX is already
          weighting-aware (snapshot_weightings.objective is baked in), so we
          render `cost.opex` directly without applying weights ourselves —
          double-counting check noted in the Dispatch.tsx code comment. */}
      {cost && (
        <section>
          <div className="flex items-center justify-between mb-2.5">
            <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">Operational expenditure (OPEX)</h3>
            <button
              onClick={() => {
                const rows = cost.by_component
                  .filter(b => b.opex > 0)
                  .map(b => [b.component, b.opex] as [string, number])
                if (curtailmentCost > 0) rows.push(['Curtailment penalty', curtailmentCost])
                downloadCSV('results_opex_by_component.csv',
                  ['component', 'opex_EUR'],
                  rows,
                )
                toast.success('Exported')
              }}
              className="flex items-center gap-1 text-[11px] text-muted hover:text-accent"
            ><Download size={11} /> CSV</button>
          </div>
          <div className="grid grid-cols-4 gap-3">
            <KPI accent label="Total OPEX"
              value={fmtCurrency(cost.opex + curtailmentCost)}
              hint={`Dispatch OPEX (${fmtCurrency(cost.opex, 1)}) + curtailment penalty (${fmtCurrency(curtailmentCost, 1)}). Matches the OPEX-side total the LP minimised, including the custom curtailment_cost term injected via extra_functionality.`} />
            <KPI label="Dispatch OPEX" value={fmtCurrency(cost.opex)}
              hint="Σ marginal_cost × dispatch over horizon (PyPSA n.statistics() 'Operational Expenditure')" />
            <KPI label="Curtailment penalty"
              value={curtailmentCost > 0 ? fmtCurrency(curtailmentCost) : '—'}
              hint="Σ curtailment_t × curtailment_cost over renewable generators that opted in. Set curtailment_cost > 0 on a renewable to start charging the optimiser for spilled energy." />
            {cost.by_component
              .filter(b => b.opex > 0)
              .sort((a, b) => b.opex - a.opex)
              .map(b => (
                <KPI key={b.component} label={b.component} value={fmtCurrency(b.opex)} />
              ))
            }
          </div>
        </section>
      )}

      {/* ── Economics: cost per period ───────────────────────── */}
      {/* Hide entirely when every bar would be 0 — keeps the layout clean on
          test networks where no cost data is configured. */}
      {economicsBars && economicsBars.length > 0
        && economicsBars.some(r => r.CAPEX > 0 || r.OPEX > 0 || r.VOLL > 0) && (
        <ChartCard
          title="Economics — system cost per period"
          hint="CAPEX · OPEX · VOLL"
          right={
            <ChartActions
              chartRef={economicsRef}
              onExportCSV={() => {
                downloadCSV('aggregated_economics.csv',
                  ['period', 'CAPEX_EUR', 'OPEX_EUR', 'VOLL_EUR'],
                  economicsBars.map(r => [r.period, r.CAPEX, r.OPEX, r.VOLL]))
                toast.success('Exported')
              }}
              filename="aggregated_economics" />
          }
          bodyClassName="p-3"
        >
          <div ref={economicsRef}>
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={economicsBars} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid {...CHART_GRID} />
                <XAxis dataKey="period" {...CHART_AXIS} />
                <YAxis {...CHART_AXIS} tickFormatter={(v: number) => fmtCurrency(v, 0)} />
                <RTooltip {...CHART_TOOLTIP} formatter={(v: number, name: string) => [fmtCurrency(v), name]} />
                <Legend {...CHART_LEGEND} />
                <Bar dataKey="CAPEX" stackId="cost" fill="#0369a1" />
                <Bar dataKey="OPEX"  stackId="cost" fill="#d97706" />
                <Bar dataKey="VOLL"  stackId="cost" fill="#dc2626" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
            <p className="text-[10px] text-muted mt-2">
              Each bar's CAPEX/OPEX is the period's full LP-objective contribution
              (PyPSA's <code>n.statistics()</code> per-period values × <code>investment_period_weightings.years</code>).
              OPEX = dispatch cost (Σ marginal_cost × p) + curtailment penalty —
              matches the "Total OPEX" KPI shown when you drill into that period.
            </p>
          </div>
        </ChartCard>
      )}

      {/* ── Generation: energy mix per period ────────────────── */}
      {generationMix && generationRows && (
        <ChartCard
          title="Generation — energy mix per period"
          right={
            <>
              <div className="inline-flex items-center rounded-[7px] bg-panel border border-border p-0.5 text-[10px]">
                <button onClick={() => setGenMode('abs')}
                  className={`px-2 h-[22px] rounded-[5px] font-medium transition-colors
                    ${genMode === 'abs' ? 'bg-bg text-text shadow-[0_1px_0_rgba(10,14,20,0.04)]' : 'text-muted hover:text-text'}`}>
                  Absolute (MWh)
                </button>
                <button onClick={() => setGenMode('pct')}
                  className={`px-2 h-[22px] rounded-[5px] font-medium transition-colors
                    ${genMode === 'pct' ? 'bg-bg text-text shadow-[0_1px_0_rgba(10,14,20,0.04)]' : 'text-muted hover:text-text'}`}>
                  % of total
                </button>
              </div>
              <ChartActions
                chartRef={generationRef}
                onExportCSV={() => {
                  const header = ['period', ...generationMix.carriers]
                  downloadCSV(`aggregated_generation_${genMode}.csv`, header,
                    generationRows.map(r => [r.period, ...generationMix.carriers.map(c => r[c] ?? 0)]))
                  toast.success('Exported')
                }}
                filename={`aggregated_generation_${genMode}`} />
            </>
          }
          bodyClassName="p-3"
        >
          <div ref={generationRef}>
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={generationRows} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid {...CHART_GRID} />
                <XAxis dataKey="period" {...CHART_AXIS} />
                <YAxis {...CHART_AXIS}
                  tickFormatter={(v: number) =>
                    genMode === 'pct' ? `${v.toFixed(0)} %` : fmtEnergy(v, 0)}
                  domain={genMode === 'pct' ? [0, 100] : undefined} />
                <RTooltip {...CHART_TOOLTIP} formatter={(v: number, name: string) =>
                  [genMode === 'pct' ? `${v.toFixed(1)} %` : fmtEnergy(v), name]} />
                <Legend {...CHART_LEGEND} />
                {generationMix.carriers.map((c, i) => (
                  <Bar key={c} dataKey={c} stackId="gen" fill={colourForCarrier(c, i)} />
                ))}
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>
      )}

      {/* ── Storage: charged / discharged per period (by carrier) ── */}
      {storageBars && storageBars.rows.length > 0 && (
        <ChartCard
          title="Storage — energy charged & discharged per period"
          hint={storageBars.carriers.length > 1
            ? `by carrier · ${storageBars.carriers.join(' · ')}`
            : 'discharge vs. charge'}
          right={
            <ChartActions
              chartRef={storageRef}
              onExportCSV={() => {
                const header = ['period',
                  ...storageBars.carriers.flatMap(c => [`${c}_discharged_MWh`, `${c}_charged_MWh`])]
                downloadCSV('aggregated_storage.csv', header,
                  storageBars.rows.map(r => [r.period,
                    ...storageBars.carriers.flatMap(c => [Number(r[`${c}_d`] ?? 0), Number(r[`${c}_c`] ?? 0)])]))
                toast.success('Exported')
              }}
              filename="aggregated_storage" />
          }
          bodyClassName="p-3"
        >
          <div ref={storageRef}>
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={storageBars.rows} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid {...CHART_GRID} />
                <XAxis dataKey="period" {...CHART_AXIS} />
                <YAxis {...CHART_AXIS} tickFormatter={(v: number) => fmtEnergy(v, 0)} />
                <RTooltip {...CHART_TOOLTIP} formatter={(v: number, name: string) => [fmtEnergy(v), name]} />
                <Legend {...CHART_LEGEND} />
                {/* Two stacks per period: discharge (positive direction)
                    stacks together, charge (gross positive bars in a
                    separate stack) stacks alongside. Each carrier gets a
                    stable colour from the shared palette. The naming
                    convention ` discharged` / ` charged` reads cleanly
                    in the legend even with multiple carriers. */}
                {storageBars.carriers.map((c, i) => (
                  <Bar key={`${c}_d`} dataKey={`${c}_d`} name={`${c} discharged`}
                    stackId="discharged"
                    fill={colourForCarrier(c, i)} radius={[3, 3, 0, 0]} />
                ))}
                {storageBars.carriers.map((c, i) => (
                  <Bar key={`${c}_c`} dataKey={`${c}_c`} name={`${c} charged`}
                    stackId="charged"
                    fill={colourForCarrier(c, i)} fillOpacity={0.55}
                    radius={[3, 3, 0, 0]} />
                ))}
              </BarChart>
            </ResponsiveContainer>
            <p className="text-[10px] text-muted mt-2">
              Bars are independent (not net) so the user sees gross energy moved in each direction.
              Discharge and charge are shown as separate stacked bars side-by-side per period;
              charge bars are rendered slightly transparent.
            </p>
          </div>
        </ChartCard>
      )}

      {/* ── Renewables: curtailment per period (by carrier) ───── */}
      {curtailmentBars && curtailmentBars.rows.length > 0
        && curtailmentBars.rows.some(r => curtailmentBars.carriers.some(c => Number(r[c] ?? 0) > 0)) && (
        <ChartCard
          title="Renewables — curtailment per period"
          hint={curtailmentBars.carriers.length > 1
            ? `by carrier · ${curtailmentBars.carriers.join(' · ')}`
            : 'Σ max(p_max_pu × p_nom − p, 0)'}
          right={
            <ChartActions
              chartRef={curtailmentRef}
              onExportCSV={() => {
                const header = ['period', ...curtailmentBars.carriers]
                downloadCSV('aggregated_curtailment.csv', header,
                  curtailmentBars.rows.map(r => [r.period,
                    ...curtailmentBars.carriers.map(c => Number(r[c] ?? 0))]))
                toast.success('Exported')
              }}
              filename="aggregated_curtailment" />
          }
          bodyClassName="p-3"
        >
          <div ref={curtailmentRef}>
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={curtailmentBars.rows} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid {...CHART_GRID} />
                <XAxis dataKey="period" {...CHART_AXIS} />
                <YAxis {...CHART_AXIS} tickFormatter={(v: number) => fmtEnergy(v, 0)} />
                <RTooltip {...CHART_TOOLTIP} formatter={(v: number, name: string) => [fmtEnergy(v), name]} />
                <Legend {...CHART_LEGEND} />
                {curtailmentBars.carriers.map((c, i) => (
                  <Bar key={c} dataKey={c} name={c} stackId="curt"
                    fill={colourForCarrier(c, i)} radius={[3, 3, 0, 0]} />
                ))}
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>
      )}

      {/* ── Load: by carrier per period ──────────────────────── */}
      {loadByCarrier && loadByCarrier.rows.length > 0 && (
        <ChartCard
          title="Load — energy demand per period by carrier"
          hint="electricity · heat · H₂"
          right={
            <ChartActions
              chartRef={loadRef}
              onExportCSV={() => {
                const header = ['period', ...loadByCarrier.carriers]
                downloadCSV('aggregated_load_by_carrier.csv', header,
                  loadByCarrier.rows.map(r => [r.period, ...loadByCarrier.carriers.map(c => r[c] ?? 0)]))
                toast.success('Exported')
              }}
              filename="aggregated_load_by_carrier" />
          }
          bodyClassName="p-3"
        >
          <div ref={loadRef}>
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={loadByCarrier.rows} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid {...CHART_GRID} />
                <XAxis dataKey="period" {...CHART_AXIS} />
                <YAxis {...CHART_AXIS} tickFormatter={(v: number) => fmtEnergy(v, 0)} />
                <RTooltip {...CHART_TOOLTIP} formatter={(v: number, name: string) => [fmtEnergy(v), name]} />
                <Legend {...CHART_LEGEND} />
                {loadByCarrier.carriers.map((c, i) => (
                  <Bar key={c} dataKey={c} stackId="load" fill={colourForCarrier(c, i)} />
                ))}
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>
      )}
    </div>
  )
}
