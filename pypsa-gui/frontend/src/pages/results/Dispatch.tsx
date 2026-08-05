import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ResponsiveContainer, AreaChart, Area, LineChart, Line, ComposedChart,
  XAxis, YAxis, CartesianGrid, Tooltip as RTooltip, Legend,
} from 'recharts'
import { Download, Flame, Wind, BatteryCharging, Zap, ChevronDown, ChevronRight, Plug, Database, Scissors } from 'lucide-react'
import toast from 'react-hot-toast'
import { resultsApi } from '../../api/simulation'
import { networkApi } from '../../api/network'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import type { Generator, Load, StorageUnit, Store, Link as LinkT } from '../../api/types'
import {
  type TSPayload, type WeightCtx, type ResultsViewMode, type Season,
  generatorGroup, aggregateTS, isRenewableCarrier, durationCurvePoints, useWeightCtx,
  weightedSum, weightedSumSplit, averageScaling, hasScaling,
  fmtEnergy, fmtCurrency, fmtPower, shortStamp, downloadCSV, downloadSVG, KPI, Seg,
  ChartCard, ChartActions,
  CHART_GRID, CHART_AXIS, CHART_LEGEND, CHART_TOOLTIP, yAxisLabel,
  SEASONS, SEASON_OF_MONTH, SEASON_ACCENT,
  WEEKLY_MIN_DAYS, MONTHLY_MIN_DAYS, WEEKDAYS,
  parseSeasonStamp, useSeasonalViewMode, colourForCarrier,
} from './shared'
import { useResultsFilter, resolveRange } from './filterContext'
import { useResultsWindow } from '../../hooks/useResultsWindow'
import DispatchStack from './DispatchStack'
import StorageHeatmap from './StorageHeatmap'
import { useFilterableTable, TableSearchBox, SortHeader } from './useFilterableTable'
import { storageCarrierKey, storageCarrierLabel } from './carrierAliases'

// Module-level constant so we don't allocate a fresh Set on every parent
// render. Passed to <DispatchStack> for non-electrical stacks where the
// electrolyser-consume series doesn't apply. A fresh `new Set()` each
// render would invalidate every downstream useMemo that lists this prop
// in its deps. QA-flagged identity hazard.
const EMPTY_STRING_SET: Set<string> = new Set()

// ── Dispatch tab ──────────────────────────────────────────────────────────────
// System overview KPIs at the top, then five collapsible group sections:
//   Thermal, Renewables, Storage (charge ↔ discharge), Loads, Links.
//
// Each group can switch between two views via a small toggle:
//   • Aggregated — single area chart of the group total
//   • Per-asset — multi-line chart with one trace per asset (top-N by mean
//     absolute power so ten thousand columns don't melt the browser)
//
// Stores get their own section since they hold energy (MWh) rather than power
// flow. Each row of the multi-line chart corresponds to a specific asset; CSV
// export emits the wide table so external tools can re-aggregate however.

const PER_ASSET_TOP_N = 12      // top N traces shown in per-asset chart
const STORAGE_GROUP_MAX = 8     // top N storages in stacked breakdown

// Storage and Stores both have two natural views:
//   • Power (signed MW): positive = discharge (production), negative = charge
//   • Energy (MWh): state of charge for storage units, energy stored for stores
// Render each as its own section so each has independent collapse state,
// per-asset toggle, and CSV export. Slightly darker shade differentiates the
// energy view from the power view at a glance.
const GROUP_STYLE: Record<GroupKey,
  { color: string; Icon: typeof Flame; unit: string }> = {
  Thermal:        { color: '#dc2626', Icon: Flame,           unit: 'MW' },
  Renewables:     { color: '#16a34a', Icon: Wind,            unit: 'MW' },
  Storage:        { color: '#7c3aed', Icon: BatteryCharging, unit: 'MW' },
  'Storage SoC':  { color: '#5b21b6', Icon: BatteryCharging, unit: 'MWh' },
  Load:           { color: '#d97706', Icon: Zap,             unit: 'MW' },
  Links:          { color: '#0ea5e9', Icon: Plug,            unit: 'MW' },
  Stores:         { color: '#a855f7', Icon: Database,        unit: 'MW' },
  'Stores Energy':{ color: '#7e22ce', Icon: Database,        unit: 'MWh' },
  // Electrolyzer reuses the Links palette so the user can connect the H2
  // production band visually to the Links section below the seasonal view.
  Electrolyzer:   { color: '#0ea5e9', Icon: Plug,            unit: 'MW' },
}

// Light palette for per-asset traces. Repeats if more traces than colours.
const TRACE_COLORS = [
  '#0ea5e9', '#16a34a', '#dc2626', '#d97706', '#7c3aed',
  '#a855f7', '#0891b2', '#65a30d', '#e11d48', '#f59e0b',
  '#9333ea', '#06b6d4',
]

type GroupKey = 'Thermal' | 'Renewables' | 'Storage' | 'Storage SoC'
              | 'Load' | 'Links' | 'Stores' | 'Stores Energy' | 'Electrolyzer'

const VIEWMODE_KEY = 'results:dispatch-viewmode'
// ── Per-carrier seasonal balance ─────────────────────────────────────────────
// For carriers other than electricity (H2, heat, …) the seasonal chart needs
// to be the energy balance AT THAT CARRIER'S BUSES, not the electricity
// stack. The carrier-specific stack uses these generic group keys:
//   • Generation        — gens at carrier-C buses (+ p)
//   • Storage           — storage units at carrier-C buses (signed)
//   • Stores            — stores at carrier-C buses (signed)
//   • Conversion in     — links whose bus1/bus2 carrier == C: +eff × p0
//   • Conversion out    — links whose bus0 carrier == C: -p0
//   • Load              — loads at carrier-C buses
// `Conversion in/out` collapse all electrolyser / heat-pump / datacenter
// link flows into a single in/out band per carrier — the user can drill into
// per-link detail via the per-carrier sections further down.
type CarrierGroupKey =
  | 'Generation' | 'Storage' | 'Stores' | 'Conversion in' | 'Conversion out' | 'Load'
const CARRIER_GROUPS: CarrierGroupKey[] = [
  'Load', 'Generation', 'Storage', 'Stores', 'Conversion in', 'Conversion out',
]
const CARRIER_SPLIT_GROUPS: CarrierGroupKey[] = ['Storage', 'Stores', 'Conversion in', 'Conversion out']
// Both ▲ and ▼ branches must be rendered for every split group — without
// them, a multi-port Link with eff_i < 0 (heat-pump bus2 drawing low-T heat)
// silently disappears from the seasonal grid. Symptom: heat chart shows
// dc_heatpump producing 95 MW to high-T (Conversion in ▲) but not the 47.5 MW
// it draws from low-T (Conversion in ▼) — mass balance breaks visually.
// Same logic the horizon-view DispatchStack fix applied. Keys are listed in
// CarrierSeasonalSection (POS_NON_GEN_KEYS / NEG_NON_GEN_KEYS).
const CARRIER_GROUP_STYLE: Record<CarrierGroupKey, { color: string }> = {
  Generation:        { color: '#16a34a' },
  Storage:           { color: '#7c3aed' },
  Stores:            { color: '#a855f7' },
  'Conversion in':   { color: '#0ea5e9' },
  'Conversion out':  { color: '#0284c7' },
  Load:              { color: '#d97706' },
}
function carrierBaseGroup(k: string): CarrierGroupKey {
  return k.replace(/ [▲▼]$/, '') as CarrierGroupKey
}
function carrierSeriesLabel(k: string): string {
  if (k === 'Storage ▲')        return 'Storage discharge'
  if (k === 'Storage ▼')        return 'Storage charge'
  if (k === 'Stores ▲')         return 'Stores discharge'
  if (k === 'Stores ▼')         return 'Stores charge'
  if (k === 'Conversion in ▲')  return 'Conversion in'
  if (k === 'Conversion out ▼') return 'Conversion out'
  return k
}

// Canonicalise a raw bus carrier string for display + lookup. Maps AC /
// electricity / electrical / '' → 'electricity' so the carrier filter
// shows ONE chip instead of three.
function canonicalCarrier(raw: string | null | undefined): string {
  const c = (raw ?? '').trim().toLowerCase()
  if (!c || c === 'ac' || c === 'electricity' || c === 'electrical') return 'electricity'
  if (c === 'h2' || c === 'hydrogen') return 'hydrogen'
  return c
}
function carrierDisplayLabel(c: string): string {
  if (c === 'electricity') return 'Electricity'
  if (c === 'hydrogen')    return 'H₂'
  if (c === 'heat')        return 'Heat'
  return c.charAt(0).toUpperCase() + c.slice(1)
}
// ── Load carrier helpers ─────────────────────────────────────────────────────
// PyPSA Loads carry a `carrier` field but the convention is loose: AC
// electrical demand may be stored as '', 'AC', or 'electricity'. We normalise
// those to a single 'electrical' bucket so the user sees ONE Electrical
// section regardless of input convention, while still preserving the
// natural distinction between electrical / H2 / heat / etc.

const LOAD_ELECTRICAL_ALIASES = new Set(['', 'ac', 'electricity', 'electric', 'electrical', 'el'])
// Substring-based matching — covers PyPSA-Eur conventions like "urban
// central heat", "low T heat", "H2 for industry", etc. Mirrored in
// ModelHorizon.tsx and the backend `_canonical_load_carrier_key`.
const LOAD_HEAT_TOKENS     = ['heat', 'thermal']
const LOAD_HYDROGEN_TOKENS = ['hydrogen', 'h2']

function loadCarrierKey(raw: string | undefined | null): string {
  const c = (raw ?? '').trim().toLowerCase()
  if (LOAD_ELECTRICAL_ALIASES.has(c)) return 'electrical'
  if (!c) return 'electrical'
  if (LOAD_HYDROGEN_TOKENS.some(tok => c.includes(tok))) return 'hydrogen'
  if (LOAD_HEAT_TOKENS.some(tok => c.includes(tok)))     return 'heat'
  return c || 'unspecified'
}
function loadCarrierLabel(key: string): string {
  if (key === 'electrical') return 'Electrical'
  if (key === 'hydrogen')   return 'Hydrogen'
  if (key === 'heat')       return 'Heat'
  if (key === 'unspecified') return 'Unspecified'
  return key.charAt(0).toUpperCase() + key.slice(1)
}
// Sort: electrical first (the default + most important), hydrogen, heat,
// then alphabetical for the long tail. Keeps the section order intuitive.
function loadCarrierSortKey(key: string): string {
  if (key === 'electrical') return '0_electrical'
  if (key === 'hydrogen')   return '1_hydrogen'
  if (key === 'heat')       return '2_heat'
  if (key === 'unspecified') return 'z_unspecified'
  return `m_${key}`
}
function loadIsElectrical(key: string): boolean {
  return key === 'electrical'
}

// ── Storage carrier helpers ─────────────────────────────────────────────────
// Storage section-ordering helper. The aliasing + display label logic lives
// in the shared `./carrierAliases` module so every results tab uses the
// SAME canonicalisation (a "battery" chip on CapacityExpansion matches
// the "Battery" section here, regardless of whether the user typed BESS /
// Li-Ion / battery). Only the section-ordering function stays local —
// it's Dispatch-specific UX.
function storageCarrierSortKey(key: string): string {
  if (key === 'battery')      return '0_battery'
  if (key === 'pumped_hydro') return '1_phs'
  if (key === 'hydrogen')     return '2_h2'
  if (key === 'heat')         return '3_heat'
  if (key === 'unspecified')  return 'z_unspecified'
  return `m_${key}`
}

// Electrolyzer / power-to-gas carrier matching. PyPSA examples + PyPSA-Eur
// use a wide variety of spellings (electrolyser, electrolyzer, "H2
// Electrolysis", "P2G", "Power-to-H2", and projects sometimes label the
// link simply with the OUTPUT carrier — "hydrogen" or "h2"). We accept any
// link whose normalised carrier contains one of these tokens; the
// substring match also handles "PEM electrolyser", "alkaline electrolyser",
// "h2_electrolysis_co2", etc.
//
// Why `hydrogen` / `h2` are safe to include here even though they could
// theoretically match H2 PIPELINES: the user-facing semantic of this
// matcher is "a link that participates in the H2-side energy balance".
// Pipelines do participate (transporting H2), so including them in the
// heatmap / dispatch-stack view is informative — and pipelines tend to
// have small flows compared to the electrolyser, so they don't visually
// drown out the actual production.
const ELECTROLYZER_TOKENS = [
  'electrol',           // electrolyser, electrolyzer, electrolysis
  'p2g', 'p2h2',
  'power-to-h2', 'power-to-gas', 'power2gas',
  'hydrogen',           // user-typed "Hydrogen" carrier (common in PyPSA-Eur)
  'h2',                 // "H2", "H2 Electrolysis", "H2 pipeline", etc.
]
function isElectrolyzerCarrier(c: string | undefined | null): boolean {
  const norm = (c ?? '').trim().toLowerCase()
  if (!norm) return false
  return ELECTROLYZER_TOKENS.some(tok => norm.includes(tok))
}

export default function Dispatch() {
  // Result source (LOPF vs AC PF) — the same global toggle LoadFlow exposes.
  // Put it in EVERY dispatch query key AND pass it to the getter so flipping
  // the source on Load Flow also re-fetches the Dispatch tab against the
  // chosen solution (was previously hardcoded LOPF here, so the toggle silently
  // had no effect on Dispatch). The backend coerces unknown sources to lopf
  // and falls back when a given table has no ac_pf variant, so this is safe
  // for the LP-only quantities too.
  const resultSource = useUIStore(s => s.resultSource)
  const currentProject = useUIStore(s => s.currentProject)
  // Positional bounds for the active Horizon filter, resolved against the
  // SNAPSHOT INDEX (not a results payload) so the nine per-snapshot queries
  // below can be windowed before any payload exists. Distinct from `range`
  // further down, which resolves against the fetched payload's own index to
  // re-slice client-side — see the comment at that call site.
  const { win, winValid } = useResultsWindow(currentProject)
  // Cost breakdown: used by the OPEX KPI strip + per-class table below. Same
  // endpoint Capacity Expansion uses, cached under the shared queryKey so we
  // don't double-fetch when both tabs are mounted. (LP-only — no source, and
  // an aggregate with no snapshot axis — not windowed.)
  const { data: cost } = useQuery({ queryKey: nk(currentProject, 'results', 'cost_breakdown'), queryFn: resultsApi.getCostBreakdown })
  const { data: gensTS } = useQuery({
    queryKey: nk(currentProject, 'results', 'generators', resultSource, win.from, win.to),
    queryFn: () => resultsApi.getGeneratorResults(resultSource, win),
    enabled: winValid,
  })
  // Curtailment = max(p_max_pu * p_nom_opt − p, 0). Computed server-side.
  // Used for the renewables KPI + the toggleable per-section plot below.
  // (LP-only — AC PF doesn't produce curtailment.) Per-snapshot but
  // `getCurtailment` takes no `range` argument — it wasn't widened by the
  // earlier plan, so this query is left unranged rather than modifying
  // api/simulation.ts from inside this task.
  const { data: curtailTS } = useQuery({ queryKey: nk(currentProject, 'results', 'curtailment'), queryFn: resultsApi.getCurtailment })
  // Storage gets BOTH SoC (MWh) AND signed power (MW). The power view is what
  // the user means by "production / consumption" — positive = discharging,
  // negative = charging. SoC is the energy reservoir level.
  const { data: storTS } = useQuery({
    queryKey: nk(currentProject, 'results', 'storage', resultSource, win.from, win.to),
    queryFn: () => resultsApi.getStorageResults(resultSource, win),
    enabled: winValid,
  })
  const { data: storPowerTS } = useQuery({
    queryKey: nk(currentProject, 'results', 'storage_dispatch', resultSource, win.from, win.to),
    queryFn: () => resultsApi.getStorageDispatchResults(resultSource, win),
    enabled: winValid,
  })
  const { data: storeTS } = useQuery({
    queryKey: nk(currentProject, 'results', 'store_dispatch', resultSource, win.from, win.to),
    queryFn: () => resultsApi.getStoreDispatchResults(resultSource, win),
    enabled: winValid,
  })
  const { data: storeETS } = useQuery({
    queryKey: nk(currentProject, 'results', 'store_energy', resultSource, win.from, win.to),
    queryFn: () => resultsApi.getStoreEnergyResults(resultSource, win),
    enabled: winValid,
  })
  const { data: loadTS } = useQuery({
    queryKey: nk(currentProject, 'results', 'loads', resultSource, win.from, win.to),
    queryFn: () => resultsApi.getLoadResults(resultSource, win),
    enabled: winValid,
  })
  // Per-link p0 (signed MW; positive = bus0→bus1). Rendered as one section
  // per link carrier so the user can compare e.g. HVDC interconnect vs
  // electrolyser dispatch separately.
  const { data: linkTS } = useQuery({
    queryKey: nk(currentProject, 'results', 'links', resultSource, win.from, win.to),
    queryFn: () => resultsApi.getLinkResults(resultSource, win),
    enabled: winValid,
  })
  // VOLL slack dispatch — non-null only when last solve ran with voll > 0
  // and the LP actually shed load. Drives the Lost Load KPI + chart below.
  // Per-snapshot but `getLostLoad` takes no `range` argument (not widened
  // by the earlier plan) — left unranged, same reasoning as curtailment.
  const { data: lostLoad } = useQuery({ queryKey: nk(currentProject, 'results', 'lost_load'), queryFn: resultsApi.getLostLoad })
  // Per-carrier economics (live network) — drives the per-carrier KPI strip
  // above each DispatchStack. Mirrors the Compare View's by_carrier split
  // (gen/charge/curtailment/lost-load) so the single-project view shows
  // the same comprehensive OPEX picture without depending on autosave.
  const { data: econByCarrier } = useQuery({
    queryKey: nk(currentProject, 'results', 'economics_by_carrier'),
    queryFn: resultsApi.getEconomicsByCarrier,
  })

  const { data: buses = [] }        = useQuery({ queryKey: nk(currentProject, 'buses'),         queryFn: networkApi.getBuses })
  const { data: generators = [] }   = useQuery({ queryKey: nk(currentProject, 'generators'),    queryFn: networkApi.getGenerators })
  const { data: storageUnits = [] } = useQuery({ queryKey: nk(currentProject, 'storage_units'), queryFn: networkApi.getStorageUnits })
  const { data: stores = [] }       = useQuery({ queryKey: nk(currentProject, 'stores'),        queryFn: networkApi.getStores })
  const { data: loads = [] }        = useQuery({ queryKey: nk(currentProject, 'loads'),         queryFn: networkApi.getLoads })
  const { data: links = [] }        = useQuery({ queryKey: nk(currentProject, 'links'),         queryFn: networkApi.getLinks })

  // Snapshot weights + period weights drive the KPI scaling. Without these,
  // a representative-week run with snapshot_weightings.generators = 52.14 shows
  // its raw 168-hour energy total (52× too small versus n.statistics()).
  // /snapshots + /investmentPeriods are fetched inside useWeightCtx (below).
  // Per-vintage build_year/p_nom_opt for assets with per-period bounds. Used by
  // the Storage SoC `%` view to scale by the period-active energy capacity
  // instead of the cumulative final-year capacity. Without this, the SoC bar in
  // the earliest period appears tiny (~25% of full) because the denominator
  // includes vintages that don't yet exist.
  const { data: vintageResults } = useQuery({
    queryKey: nk(currentProject, 'vintage_results'),
    queryFn: networkApi.listVintageResults,
  })

  // Categorise generators into Thermal vs Renewables once.
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
  const storeNames = useMemo(() => new Set((stores as Store[]).map(s => s.name)), [stores])
  const loadNames = useMemo(() => new Set((loads as Load[]).map(l => l.name)), [loads])
  const linkNames = useMemo(() => new Set((links as LinkT[]).map(l => l.name)), [links])

  // Names of storage units on the ELECTRICAL bus — i.e. anything that
  // charges from / discharges to the electricity balance. Permissive
  // filter: we EXCLUDE only the carriers we know are non-electrical
  // (hydrogen, heat) so storage units with a custom or unspecified
  // carrier default to electrical. Consumed by `series.Storage` /
  // `series['Storage SoC']` and the seasonal stacked-area view so heat /
  // H2 reservoir activity doesn't leak into the electrical-balance chart.
  const electricalStorageNames = useMemo(() => {
    const out = new Set<string>()
    for (const s of storageUnits as StorageUnit[]) {
      const c = storageCarrierKey(s.carrier)
      if (c === 'hydrogen' || c === 'heat') continue
      out.add(s.name)
    }
    return out
  }, [storageUnits])

  // Electrolyzer links: PyPSA convention is the `carrier` field on the Link
  // contains one of several common spellings (electrolyser, electrolyzer,
  // 'H2 Electrolysis', …). We match case-insensitively against a small
  // alias set so all of them collapse into the same "electrolyzer" bucket
  // for the heatmap + dispatch-stack + seasonal views. Defined alongside
  // `linkNames` so downstream `useMemo` chains (notably `series`) can rely
  // on a stable identity reference.
  const electrolyzerNames = useMemo(() => {
    const out = new Set<string>()
    for (const k of links as LinkT[]) {
      if (isElectrolyzerCarrier(k.carrier)) out.add(k.name)
    }
    return out
  }, [links])

  // Group storage units by carrier (battery / hydrogen / heat / …).
  const storageGroups = useMemo(() => {
    const byCarrier = new Map<string, StorageUnit[]>()
    for (const s of storageUnits as StorageUnit[]) {
      const c = storageCarrierKey(s.carrier)
      const bucket = byCarrier.get(c) ?? []
      bucket.push(s)
      byCarrier.set(c, bucket)
    }
    return Array.from(byCarrier.entries())
      .sort(([a], [b]) => storageCarrierSortKey(a).localeCompare(storageCarrierSortKey(b)))
      .map(([carrier, group]) => ({
        carrier,
        assets: group,
        nameSet: new Set(group.map(g => g.name)),
      }))
  }, [storageUnits])

  // Group loads by carrier (electrical AC, H2, heat, …). The user wants
  // these visually separated so a 200 MW electrical load isn't lumped
  // together with a 50 MW H2 demand on the same axis. PyPSA's default load
  // carrier is empty (treated as electrical); we collapse empty / 'ac' /
  // 'electricity' into a single canonical "Electrical" bucket so a project
  // that mixes the three conventions still renders one electrical section.
  const loadGroups = useMemo(() => {
    const byCarrier = new Map<string, Load[]>()
    for (const l of loads as Load[]) {
      const c = loadCarrierKey(l.carrier)
      const bucket = byCarrier.get(c) ?? []
      bucket.push(l)
      byCarrier.set(c, bucket)
    }
    return Array.from(byCarrier.entries())
      .sort(([a], [b]) => loadCarrierSortKey(a).localeCompare(loadCarrierSortKey(b)))
      .map(([carrier, group]) => ({
        carrier,
        assets: group,
        nameSet: new Set(group.map(g => g.name)),
      }))
  }, [loads])

  // ── Per-bus-carrier grouping ───────────────────────────────────────────
  // Builds one entry per distinct bus.carrier value (AC / heat / H2 / …)
  // containing the names of every Generator / Load / StorageUnit attached
  // to a bus of that carrier. Drives the per-carrier DispatchStack
  // rendering below — one chart per bus carrier so heat-bus generation
  // doesn't appear in the electrical-bus stack and vice versa.
  //
  // Falls back to a single 'all' entry when no buses are loaded (e.g.
  // mid-mount) so the section still renders something instead of
  // disappearing while queries resolve.
  const busCarrierGroups = useMemo(() => {
    const carrierByBus = new Map<string, string>()
    for (const b of buses as Array<{ name: string; carrier?: string }>) {
      // Canonicalise carrier strings before keying — without this,
      // a network with one bus tagged `'AC'` and another tagged `'ac'`
      // would split into two separate DispatchStacks for the same
      // physical carrier. Lowercase + trim gives the user a single
      // chart per logical carrier regardless of spelling. QA-flagged
      // identity hazard.
      const c = ((b.carrier ?? '').trim() || 'unspecified').toLowerCase()
      carrierByBus.set(b.name, c)
    }
    const allCarriers = new Set<string>(carrierByBus.values())
    if (allCarriers.size === 0) return []  // no buses loaded yet
    // Generators have one bus, loads have one bus, storage has one bus —
    // map each component to its bus's carrier. `busNames` tracks which buses
    // sit on each carrier so the DispatchStack can attribute link inflows /
    // outflows correctly: any link with at least one port on a busNames entry
    // contributes to that carrier's balance (electrolyzer → H2; heat-pump →
    // heat; datacenter → heat low-T). Without this set, cross-carrier links
    // disappear from the dispatch chart entirely.
    const buckets = new Map<string, { gens: Set<string>; loads: Set<string>; storage: Set<string>; stores: Set<string>; busNames: Set<string> }>()
    for (const c of allCarriers) buckets.set(c, { gens: new Set(), loads: new Set(), storage: new Set(), stores: new Set(), busNames: new Set() })
    for (const [busName, c] of carrierByBus) {
      buckets.get(c)?.busNames.add(busName)
    }
    for (const g of generators as Generator[]) {
      const c = carrierByBus.get(g.bus) ?? 'unspecified'
      buckets.get(c)?.gens.add(g.name)
    }
    for (const l of loads as Load[]) {
      const c = carrierByBus.get(l.bus) ?? 'unspecified'
      buckets.get(c)?.loads.add(l.name)
    }
    for (const s of storageUnits as StorageUnit[]) {
      const c = carrierByBus.get(s.bus) ?? 'unspecified'
      buckets.get(c)?.storage.add(s.name)
    }
    // PyPSA Store is the "energy reservoir + separate charge/discharge Link"
    // model used by PyPSA-Eur for H2, methane, and heat reservoirs (where
    // StorageUnit's combined gen+load doesn't fit). Bucket alongside
    // StorageUnit so the per-carrier panel sees BOTH types of storage.
    for (const s of stores as Store[]) {
      const c = carrierByBus.get(s.bus) ?? 'unspecified'
      buckets.get(c)?.stores.add(s.name)
    }
    // Sort: AC first (the most common starting point), then everything else
    // alphabetically. Carrier keys are already lower-cased above so no
    // re-normalisation needed here. Empty buckets (a carrier with buses
    // but no gens / loads / storage) still appear so the user knows the
    // carrier is configured.
    return [...buckets.entries()]
      .sort(([a], [b]) => {
        if (a === 'ac') return -1
        if (b === 'ac') return 1
        return a.localeCompare(b)
      })
      .map(([carrier, sets]) => ({ carrier, ...sets }))
  }, [buses, generators, loads, storageUnits, stores])

  // Group links by carrier (DC, H2 pipeline, electrolyser, …). Each carrier
  // gets its own Dispatch section so the user can compare types side-by-side
  // rather than seeing one giant blue stack of mixed link kinds. Empty
  // carrier strings collapse into a synthetic 'unspecified' bucket. The list
  // is sorted alphabetically for stable ordering across re-renders.
  const linkGroups = useMemo(() => {
    const byCarrier = new Map<string, LinkT[]>()
    for (const k of links as LinkT[]) {
      const c = (k.carrier && k.carrier.trim()) || 'unspecified'
      const bucket = byCarrier.get(c) ?? []
      bucket.push(k)
      byCarrier.set(c, bucket)
    }
    return Array.from(byCarrier.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([carrier, group]) => ({
        carrier,
        assets: group,
        nameSet: new Set(group.map(g => g.name)),
      }))
  }, [links])

  // Horizon filter — applies to every aggregation below. resolveRange picks
  // the first non-null TSPayload to derive bounds from, since all result
  // series share `n.snapshots` as the index. The parallel `periods` array
  // (multi-period only) lets resolveRange narrow to a contiguous period slice
  // when the user has picked one from the strip above.
  const filter = useResultsFilter()
  const refTs = (gensTS as TSPayload | null)
              ?? (loadTS as TSPayload | null)
              ?? (storTS as TSPayload | null)
  const refIndex = refTs?.index ?? []
  const refPeriods = refTs?.periods
  const range = resolveRange(refIndex, filter, refPeriods)

  const durationChartRef = useRef<HTMLDivElement | null>(null)
  // Load- and residual-load-duration curves. Total load and residual load
  // (load − variable-renewable generation) are each sorted descending over the
  // (range-sliced) horizon. The residual curve is the net demand the
  // dispatchable + storage fleet must actually meet — its peak sizes firm
  // capacity, its tail flags hours of renewable surplus. Each series is sorted
  // INDEPENDENTLY (standard duration-curve convention; x = % of hours).
  const durationCurves = useMemo(() => {
    const load = aggregateTS(loadTS as TSPayload | null, loadNames, range)
    if (!load || load.length === 0) return null
    const vre = aggregateTS(gensTS as TSPayload | null, groupCols.renew, range)
    const n = load.length
    const loadArr = new Array<number>(n)
    const resArr = new Array<number>(n)
    for (let i = 0; i < n; i++) {
      const l = load[i].value
      loadArr[i] = l
      resArr[i] = l - (vre ? (vre[i]?.value ?? 0) : 0)
    }
    const loadPts = durationCurvePoints(loadArr)
    const resPts = durationCurvePoints(resArr)
    const hasVre = !!vre && vre.some(v => Math.abs(v.value) > 1e-9)
    return {
      hasVre,
      rows: loadPts.map((p, i) => ({
        pct: p.pct, load: p.value, residual: resPts[i].value,
      })),
    }
  }, [loadTS, gensTS, loadNames, groupCols.renew, range])

  // Weight context — assembled once and shared across every weightedSum call.
  // snapshotPeriods comes from any /results/* TS payload (they all carry the
  // same parallel periods array), and falls back to /api/network/snapshots
  // when no result series is loaded yet.
  const { weightCtx } = useWeightCtx(
    (gensTS as TSPayload | null)
      ?? (loadTS as TSPayload | null)
      ?? (storTS as TSPayload | null)
      ?? null,
  )

  // Track whether scaling is non-trivial — drives the "× factor" hint on KPIs.
  const scaled = hasScaling(weightCtx)
  const avgScale = useMemo(
    () => averageScaling(weightCtx, refIndex, range, 'generators'),
    [weightCtx, refIndex, range.from, range.to],
  )
  // Tooltip text shown on every weighted energy KPI when scaling is active.
  // Tells the user exactly how the displayed total relates to the LP output.
  const scaleHint = scaled
    ? ` (× ${avgScale.toFixed(2)} avg weighting applied — see snapshot_weightings × investment_period_weightings.years)`
    : ''

  // Aggregated time-series per group, scoped to the filter range.
  //   Storage      → signed dispatch (MW), production+consumption view
  //   Storage SoC  → state of charge (MWh)
  //   Stores       → signed power flow (MW)
  //   Stores Energy → state of energy (MWh)
  const series = useMemo(() => {
    // Electrolyzer p0 is positive when consuming electricity at bus0. For
    // the seasonal stacked-area chart we want it BELOW the zero line (a
    // sink, like storage_charge), so we sign-flip the aggregated values
    // here. The split-into-▲/▼ logic in seasonalView then routes the
    // negative values into the ▼ slot automatically; `Electrolyzer ▲`
    // stays at 0 and is filtered out by the `present` check.
    const electroAgg = aggregateTS(linkTS as TSPayload | null, electrolyzerNames, range)
    const electroFlipped = electroAgg
      ? electroAgg.map(r => ({ snapshot: r.snapshot, value: -r.value }))
      : null
    return {
      Thermal:           aggregateTS(gensTS  as TSPayload | null, groupCols.thermal, range),
      Renewables:        aggregateTS(gensTS  as TSPayload | null, groupCols.renew,   range),
      // Catch-all Storage / Storage SoC keys feed the SEASONAL view's
      // electricity-balance stack ("Storage discharge / Storage charge")
      // and the top-of-page Storage KPIs. Both are electricity-balance
      // concepts — heat reservoirs and H2 storage have their own per-carrier
      // sections below, so filter the catch-all to electrical storage only.
      // Otherwise a heat-storage unit silently leaks into the seasonal
      // chart as if it were a battery.
      Storage:           aggregateTS(storPowerTS as TSPayload | null, electricalStorageNames,  range),
      'Storage SoC':     aggregateTS(storTS  as TSPayload | null, electricalStorageNames,      range),
      Stores:            aggregateTS(storeTS as TSPayload | null, storeNames,        range),
      'Stores Energy':   aggregateTS(storeETS as TSPayload | null, storeNames,       range),
      Load:              aggregateTS(loadTS  as TSPayload | null, loadNames,         range),
      // Links: sum of p0 across every link in the network. Per-carrier
      // aggregations are rendered separately via `linkSeriesByCarrier` below
      // so the user sees one chart per link type; this catch-all isn't
      // surfaced in the UI but is kept here so `series[gk]` lookups in the
      // seasonal-chart code don't crash on missing keys.
      Links:             aggregateTS(linkTS as TSPayload | null, linkNames, range),
      Electrolyzer:      electroFlipped,
    } as Record<GroupKey, ReturnType<typeof aggregateTS>>
  },
  [gensTS, storTS, storPowerTS, storeTS, storeETS, loadTS, linkTS,
   groupCols, electricalStorageNames, storeNames, loadNames, linkNames, electrolyzerNames,
   range.from, range.to])

  // Per-carrier aggregated link series. Computed alongside the catch-all
  // above so each GroupSection gets its own aggregated trace.
  const linkSeriesByCarrier = useMemo(() => linkGroups.map(g => ({
    carrier: g.carrier,
    assets: g.assets,
    nameSet: g.nameSet,
    agg: aggregateTS(linkTS as TSPayload | null, g.nameSet, range),
  })), [linkGroups, linkTS, range.from, range.to])

  // Per-carrier aggregated load series — same pattern as links. Computed
  // here (not inside `series`) so the body can map directly over carriers.
  const loadSeriesByCarrier = useMemo(() => loadGroups.map(g => ({
    carrier: g.carrier,
    assets: g.assets,
    nameSet: g.nameSet,
    agg: aggregateTS(loadTS as TSPayload | null, g.nameSet, range),
  })), [loadGroups, loadTS, range.from, range.to])

  // Per-carrier aggregated storage series — for both signed power (Storage)
  // and state-of-charge (Storage SoC) so each carrier gets its own pair of
  // sections matching the existing storage-power / storage-SoC layout.
  const storageSeriesByCarrier = useMemo(() => storageGroups.map(g => ({
    carrier: g.carrier,
    assets: g.assets,
    nameSet: g.nameSet,
    aggPower: aggregateTS(storPowerTS as TSPayload | null, g.nameSet, range),
    aggSoC: aggregateTS(storTS as TSPayload | null, g.nameSet, range),
  })), [storageGroups, storPowerTS, storTS, range.from, range.to])


  // Renewable curtailment time-series — same filter range as everything else.
  // Aggregated over renewable generators only so we don't lump in unused
  // thermal headroom (which the backend also computes as max(p_max−p, 0) but
  // means something completely different).
  const curtailmentSeries = useMemo(
    () => aggregateTS(curtailTS as TSPayload | null, groupCols.renew, range),
    [curtailTS, groupCols.renew, range.from, range.to],
  )

  // Aggregated lost-load series — sum across all VOLL slack buses per
  // snapshot, scoped to the filter range. Used by both the chart and the
  // displayed totals so the numbers stay consistent.
  const lostLoadSeries = useMemo(() => {
    const ts = lostLoad as { index: string[]; columns: string[]; data: number[][] } | null | undefined
    if (!ts || ts.data.length === 0) return null
    // Reuse aggregateTS by treating every column as included (Set of all cols).
    const cols = new Set(ts.columns)
    return aggregateTS(ts as TSPayload, cols, range)
  }, [lostLoad, range.from, range.to])

  // Filter-aware totals — WEIGHTED. The lostLoad payload already contains a
  // raw total but the user-narrowed filter recomputes from the series, and
  // we apply snapshot+period weights so reps-week runs show annualised lost
  // load rather than the 168-hour raw sum.
  const lostLoadTotals = useMemo(() => {
    if (!lostLoad) return null
    const ts = lostLoad as TSPayload | null | undefined
    if (!ts || ts.data.length === 0) {
      // No filter movement, no series — fall back to whatever the backend
      // reported (it's already an annualised number when present).
      const ll = lostLoad as unknown as { total_mwh?: number; total_cost_eur?: number }
      return ll ? { mwh: ll.total_mwh ?? 0, cost: ll.total_cost_eur ?? 0 } : null
    }
    // Σ max(p,0) × weight — slack generators only inject so values are ≥ 0,
    // but clip defensively in case PyPSA writes a small negative.
    const cols = new Set(ts.columns)
    const split = weightedSumSplit(ts, cols, weightCtx, range, 'generators')
    const mwh = split.positive ?? 0
    const llMeta = lostLoad as unknown as { total_mwh?: number; total_cost_eur?: number }
    const voll = (llMeta.total_mwh && llMeta.total_mwh > 0)
      ? ((llMeta.total_cost_eur ?? 0) / llMeta.total_mwh) : 0
    return { mwh, cost: mwh * voll }
  }, [lostLoad, weightCtx, range.from, range.to])

  // Per-carrier lost-load split. The backend's VOLL slack lives on every
  // bus, so `lost_load_t.columns` carries bus names across electrical, H2,
  // heat, etc. `bus_carriers` (added to /results/lost_load) maps each bus
  // to its carrier string; we group columns by carrier and sum the
  // weighted dispatch so the user sees a per-carrier breakdown of unmet
  // demand instead of one mixed total.
  const lostLoadByCarrier = useMemo(() => {
    if (!lostLoad) return null
    const ts = lostLoad as TSPayload | null | undefined
    if (!ts || ts.data.length === 0) return null
    const busCarriers = (lostLoad as unknown as { bus_carriers?: Record<string, string> }).bus_carriers ?? {}
    if (Object.keys(busCarriers).length === 0) return null
    // Group columns by canonical carrier key.
    const groups = new Map<string, Set<string>>()
    for (const col of ts.columns) {
      const carrier = loadCarrierKey(busCarriers[col] ?? '')
      const bucket = groups.get(carrier) ?? new Set<string>()
      bucket.add(col)
      groups.set(carrier, bucket)
    }
    const llMeta = lostLoad as unknown as { total_mwh?: number; total_cost_eur?: number }
    const voll = (llMeta.total_mwh && llMeta.total_mwh > 0)
      ? ((llMeta.total_cost_eur ?? 0) / llMeta.total_mwh) : 0
    const out: Array<{ carrier: string; mwh: number; cost: number }> = []
    for (const [carrier, cols] of groups) {
      const split = weightedSumSplit(ts, cols, weightCtx, range, 'generators')
      const mwh = split.positive ?? 0
      if (mwh > 0) out.push({ carrier, mwh, cost: mwh * voll })
    }
    out.sort((a, b) => loadCarrierSortKey(a.carrier).localeCompare(loadCarrierSortKey(b.carrier)))
    return out
  }, [lostLoad, weightCtx, range.from, range.to])

  // Total curtailment cost over the horizon:
  //   Σ_g  curtailment_cost_g  ×  Σ_t  curtailment_t_g × w_t
  // Per-generator because each renewable can carry its own curtailment_cost.
  // Apply each row's weight independently — sum to one accumulator per
  // column-of-interest, then × cost. Uses 'objective' weighting because
  // curtailment_cost is a cost coefficient in the LP objective — same
  // weighting basis as n.statistics()['Operational Expenditure'].
  const curtailmentCost = useMemo(() => {
    const ts = curtailTS as TSPayload | null
    if (!ts || ts.data.length === 0) return 0
    const costByName = new Map<string, number>()
    for (const g of generators as Generator[]) {
      const c = (g as unknown as { curtailment_cost?: number }).curtailment_cost
      if (c && Number.isFinite(c) && c > 0) costByName.set(g.name, c)
    }
    if (costByName.size === 0) return 0
    // Reuse weightedSum per column (set-of-one), accumulate cost × value.
    let total = 0
    for (const [col, cost] of costByName) {
      const summed = weightedSum(ts, new Set([col]), weightCtx, range, 'objective')
      if (summed != null) total += cost * summed
    }
    return total
  }, [curtailTS, generators, weightCtx, range.from, range.to])

  // KPIs computed with weight-aware aggregation. Each Σ MWh KPI now applies
  // snapshot_weightings.generators × investment_period_weightings.years so
  // the displayed total is "annualised" rather than "solved-hours raw" — the
  // latter would be 52× too small on a representative-week run.
  //
  // Storage discharge/charge are the asymmetric halves of the signed dispatch
  // series. Split-and-weight in one pass via weightedSumSplit so the two
  // KPIs see the same row range and same weight multipliers.
  const kpis = useMemo(() => {
    const thermal   = weightedSum(gensTS as TSPayload | null, groupCols.thermal, weightCtx, range, 'generators')
    const renewable = weightedSum(gensTS as TSPayload | null, groupCols.renew,   weightCtx, range, 'generators') ?? 0
    const load      = weightedSum(loadTS as TSPayload | null, loadNames,         weightCtx, range, 'generators')
    // Per-carrier load energy — sums Σ p_set × weights only over loads in
    // that carrier bucket. Lets the user see electrical vs H2 vs heat
    // demand separately instead of one mixed-unit total that's hard to
    // interpret on multi-carrier networks.
    const loadByCarrier: Record<string, number | null> = {}
    for (const g of loadGroups) {
      loadByCarrier[g.carrier] = weightedSum(
        loadTS as TSPayload | null, g.nameSet, weightCtx, range, 'generators',
      )
    }
    // KPI storage discharge/charge is the electricity-balance figure —
    // heat / H2 reservoirs aren't part of the MWh-electrical accounting,
    // so exclude them. Per-carrier storage totals live in the per-carrier
    // sections further down.
    const storageSplit = weightedSumSplit(
      storPowerTS as TSPayload | null, electricalStorageNames, weightCtx, range, 'generators',
    )
    const curtailEnergy = weightedSum(
      curtailTS as TSPayload | null, groupCols.renew, weightCtx, range, 'generators',
    )
    // Curtailment % = curtailed / (delivered + curtailed). Renewables and
    // curtailment use the same weighting basis, so the ratio is invariant
    // to scaling — but the numerator+denominator pair still need consistent
    // units, hence both weighted sums.
    const potential = renewable + (curtailEnergy ?? 0)
    const curtailPct = curtailEnergy != null && potential > 0
      ? (curtailEnergy / potential) * 100
      : null
    return {
      thermal,
      renewable,
      load,
      loadByCarrier,
      totalGen: (thermal ?? 0) + renewable,
      curtailEnergy,
      curtailPct,
      storageDischarge: storageSplit.positive,
      storageCharge:    storageSplit.negative,
    }
  }, [gensTS, loadTS, storPowerTS, curtailTS, groupCols, loadNames, electricalStorageNames,
      loadGroups, weightCtx, range.from, range.to])

  // ── Per-carrier seasonal balance (Bug 2) ────────────────────────────────
  // The user can pick ONE OR MORE bus carriers (Electricity / H₂ / Heat / …)
  // and gets one 4-quadrant seasonal grid per selected carrier. Each grid
  // shows the BALANCE at that carrier's buses — generation + storage
  // discharge + cross-carrier "Conversion in" stacking up; storage charge +
  // "Conversion out" + load stacking down. Defaults to all available
  // carriers so the user immediately sees the multi-carrier picture
  // without having to discover the chip.

  // Bus → canonical carrier lookup. canonicalCarrier collapses common
  // electricity-spelling variants (AC / electricity / electrical / "") and
  // H2 variants into a single key, so a network with mixed 'AC' and
  // 'electricity' buses still shows ONE Electricity chip.
  const busCarrier = useMemo(() => {
    const m = new Map<string, string>()
    for (const b of buses as Array<{ name: string; carrier?: string }>) {
      m.set(b.name, canonicalCarrier(b.carrier))
    }
    return m
  }, [buses])

  const availableCarriers = useMemo(() => {
    const s = new Set<string>()
    for (const c of busCarrier.values()) s.add(c)
    // Stable order: electricity → hydrogen → heat → others (alphabetical).
    const order = (c: string): string =>
      c === 'electricity' ? '0' : c === 'hydrogen' ? '1' : c === 'heat' ? '2' : `m_${c}`
    return Array.from(s).sort((a, b) => order(a).localeCompare(order(b)))
  }, [busCarrier])

  // Default to ALL available carriers selected — surfaces the multi-carrier
  // picture by default. Stored as Set so toggle is O(1).
  const [selectedCarriers, setSelectedCarriers] = useState<Set<string>>(() => new Set())
  // Hydrate selection once carriers list lands. Don't overwrite later user
  // toggles — this only fires when the selection is empty AND we now know
  // the available carriers.
  useEffect(() => {
    if (selectedCarriers.size === 0 && availableCarriers.length > 0) {
      setSelectedCarriers(new Set(availableCarriers))
    }
  }, [availableCarriers, selectedCarriers.size])

  type SelectableSeason = Season | 'all'
  const [selectedSeason, setSelectedSeason] = useState<SelectableSeason>('all')

  // Per-link metadata for cross-carrier balance: bus0/1/2 carriers + the
  // matching efficiencies. PyPSA convention: p1 = -efficiency × p0 (so at
  // bus1 the net injection is +efficiency × p0), p2 = -efficiency2 × p0
  // (net injection +efficiency2 × p0; eff2 can be NEGATIVE — heat pump
  // pumps heat FROM bus2 to bus1, which a negative eff2 encodes).
  const linkMeta = useMemo(() => {
    const m = new Map<string, {
      c0: string; c1: string; c2: string | null
      eff: number; eff2: number
    }>()
    for (const k of links as Array<{
      name: string; bus0?: string; bus1?: string; bus2?: string
      efficiency?: number; efficiency2?: number
    }>) {
      const c0 = busCarrier.get(k.bus0 ?? '') ?? ''
      const c1 = busCarrier.get(k.bus1 ?? '') ?? ''
      const c2 = k.bus2 && busCarrier.get(k.bus2) ? busCarrier.get(k.bus2)! : null
      m.set(k.name, {
        c0, c1, c2,
        eff: Number.isFinite(k.efficiency as number) ? Number(k.efficiency) : 1,
        eff2: Number.isFinite(k.efficiency2 as number) ? Number(k.efficiency2) : 1,
      })
    }
    return m
  }, [links, busCarrier])

  // Per-carrier signed series. For each carrier C, sums the relevant
  // generator / storage / store / link / load activity at C buses over the
  // filter range. Returns one entry per CARRIER_GROUP_KEY; the seasonal
  // builder downstream buckets these by week-hour / month-day-hour and
  // averages.
  //
  // Generation is broken DOWN by component carrier (gas / solar / wind / …)
  // — kept in `generationByGenCarrier` keyed by gen carrier name — so the
  // weekly / monthly seasonal stack can render one Area per fuel type with
  // its own carrier-resolved colour (matching the horizon DispatchStack).
  // Without this breakdown the old aggregated green band hides the fuel mix
  // entirely on weekly / monthly views.
  type CarrierBalance = Partial<Record<CarrierGroupKey, ReturnType<typeof aggregateTS>>> & {
    generationByGenCarrier?: Map<string, ReturnType<typeof aggregateTS>>
  }
  const seriesByCarrier = useMemo(() => {
    const out = new Map<string, CarrierBalance>()
    if (availableCarriers.length === 0) return out

    // Pre-bucket each component by canonical carrier. For generators we
    // ALSO bucket by (busCarrier, genCarrier) so weekly / monthly views can
    // stack each fuel type independently rather than rolling them into one
    // green "Generation" band.
    const gensByC = new Map<string, Set<string>>()
    const gensByCByGenCarrier = new Map<string, Map<string, Set<string>>>()
    for (const g of generators as Generator[]) {
      const c = busCarrier.get((g as unknown as { bus?: string }).bus ?? '') ?? ''
      if (!c) continue
      let s = gensByC.get(c); if (!s) { s = new Set(); gensByC.set(c, s) }
      s.add(g.name)
      const gc = ((g as unknown as { carrier?: string }).carrier ?? 'other').toLowerCase() || 'other'
      let byGC = gensByCByGenCarrier.get(c)
      if (!byGC) { byGC = new Map(); gensByCByGenCarrier.set(c, byGC) }
      let names = byGC.get(gc); if (!names) { names = new Set(); byGC.set(gc, names) }
      names.add(g.name)
    }
    const loadsByC = new Map<string, Set<string>>()
    for (const l of loads as Load[]) {
      const c = busCarrier.get((l as unknown as { bus?: string }).bus ?? '') ?? ''
      if (!c) continue
      let s = loadsByC.get(c); if (!s) { s = new Set(); loadsByC.set(c, s) }
      s.add(l.name)
    }
    const storageByC = new Map<string, Set<string>>()
    for (const su of storageUnits as StorageUnit[]) {
      const c = busCarrier.get((su as unknown as { bus?: string }).bus ?? '') ?? ''
      if (!c) continue
      let s = storageByC.get(c); if (!s) { s = new Set(); storageByC.set(c, s) }
      s.add(su.name)
    }
    const storesByC = new Map<string, Set<string>>()
    for (const st of stores as Store[]) {
      const c = busCarrier.get((st as unknown as { bus?: string }).bus ?? '') ?? ''
      if (!c) continue
      let s = storesByC.get(c); if (!s) { s = new Set(); storesByC.set(c, s) }
      s.add(st.name)
    }

    // Per-link contribution to each carrier's balance. A link can touch up
    // to 3 distinct carriers; we accumulate the carrier-specific signed
    // contribution from p0 with the right ± and ± efficiency factor.
    //
    // Implementation: rather than re-aggregate each carrier's link
    // contribution separately (which would walk linkTS once per carrier),
    // we walk linkTS ONCE and emit per-(carrier, row) signed values into a
    // shared scratch buffer, then materialise as aggregated series.
    type Per = { idx: string[]; vals: Map<string, Float64Array> }  // carrier → MW per row
    const linkContribIn:  Per = { idx: [], vals: new Map() }
    const linkContribOut: Per = { idx: [], vals: new Map() }
    const lt = linkTS as TSPayload | null
    if (lt && lt.index && lt.data) {
      linkContribIn.idx = lt.index
      linkContribOut.idx = lt.index
      const nRows = lt.data.length
      for (const c of availableCarriers) {
        linkContribIn.vals.set(c, new Float64Array(nRows))
        linkContribOut.vals.set(c, new Float64Array(nRows))
      }
      // PER-PORT classification by SIGN — NOT a net-per-link coefficient.
      // A multi-port link contributes to BOTH the in (production) and out
      // (consumption) bands when its ports point opposite ways on the same
      // carrier. The heat pump (bus1=highT eff=2, bus2=lowT eff2=-1, both
      // 'heat') must show +2×p0 in Conversion in AND −1×p0 in Conversion out;
      // a net coefficient (+1×p0) would hide the low-T draw entirely, leaving
      // the seasonal Total-demand line undershooting the visible stack. Each
      // port's signed contribution is routed by its own sign: positive →
      // Conversion in, negative → Conversion out (stored negative for
      // below-axis rendering). This matches the KPI panel's per-port logic.
      const routePort = (carrier: string, row: number, contrib: number) => {
        if (contrib > 0) {
          const arr = linkContribIn.vals.get(carrier)
          if (arr) arr[row] += contrib
        } else if (contrib < 0) {
          const arr = linkContribOut.vals.get(carrier)
          if (arr) arr[row] += contrib  // negative → below-axis
        }
      }
      for (let col = 0; col < lt.columns.length; col++) {
        const lname = lt.columns[col]
        const meta = linkMeta.get(lname)
        if (!meta) continue
        for (let row = 0; row < nRows; row++) {
          const p0 = lt.data[row][col]
          if (!Number.isFinite(p0)) continue
          // bus0: link draws p0 from this carrier (coeff −1).
          if (meta.c0) routePort(meta.c0, row, -p0)
          // bus1: link injects eff × p0 into this carrier.
          if (meta.c1) routePort(meta.c1, row, meta.eff * p0)
          // bus2: eff2 × p0 — eff2 can be negative (heat-pump low-T draw).
          if (meta.c2) routePort(meta.c2, row, meta.eff2 * p0)
        }
      }
    }

    // Convert a Float64Array of per-row MW into the AggSeries shape
    // (Array<{snapshot, value}>) so seasonalView's bucketing code can
    // consume it directly. The filter range is honoured by truncating
    // here — aggregateTS does the same for component-set aggregations.
    const toAggSeries = (
      idx: string[], values: Float64Array | undefined,
    ): ReturnType<typeof aggregateTS> => {
      if (!values || idx.length === 0) return null
      const lastRow = idx.length - 1
      const rFrom = Math.max(0, Math.min(range.from, lastRow))
      const rTo = Math.max(0, Math.min(range.to, lastRow))
      if (rFrom > rTo) return null
      const arr: Array<{ snapshot: string; value: number }> = []
      for (let r = rFrom; r <= rTo; r++) {
        const v = values[r]
        if (!Number.isFinite(v)) continue
        arr.push({ snapshot: idx[r], value: v })
      }
      return arr.length > 0 ? arr : null
    }

    for (const c of availableCarriers) {
      const balance: CarrierBalance = {}
      // Generation — positive p only (curtailment / pumping excursions
      // already handled by the existing absolute-mode FLH; here we want
      // the production stack only, matching the electricity Thermal +
      // Renewables convention).
      const gens = gensByC.get(c)
      if (gens && gens.size > 0) {
        balance.Generation = aggregateTS(gensTS as TSPayload | null, gens, range)
      }
      // Per-component-carrier breakdown — one aggregated series per gen
      // carrier (gas, solar, wind, …) so the seasonal stack can render each
      // fuel as its own Area with carrier-coloured fill. The aggregated
      // `balance.Generation` is still kept above as a fallback in case the
      // seasonal renderer needs a single-band path.
      const byGC = gensByCByGenCarrier.get(c)
      if (byGC && byGC.size > 0) {
        const m = new Map<string, ReturnType<typeof aggregateTS>>()
        for (const [gc, names] of byGC) {
          m.set(gc, aggregateTS(gensTS as TSPayload | null, names, range))
        }
        balance.generationByGenCarrier = m
      }
      const sus = storageByC.get(c)
      if (sus && sus.size > 0) {
        balance.Storage = aggregateTS(storPowerTS as TSPayload | null, sus, range)
      }
      const stos = storesByC.get(c)
      if (stos && stos.size > 0) {
        balance.Stores = aggregateTS(storeTS as TSPayload | null, stos, range)
      }
      const lds = loadsByC.get(c)
      if (lds && lds.size > 0) {
        balance.Load = aggregateTS(loadTS as TSPayload | null, lds, range)
      }
      balance['Conversion in']  = toAggSeries(linkContribIn.idx,  linkContribIn.vals.get(c))
      balance['Conversion out'] = toAggSeries(linkContribOut.idx, linkContribOut.vals.get(c))
      out.set(c, balance)
    }
    return out
  }, [availableCarriers, busCarrier, linkMeta, generators, storageUnits, stores, loads,
      gensTS, storPowerTS, storeTS, loadTS, linkTS, range.from, range.to])

  // ── Seasonal view mode (weekly / monthly / full timeframe) ─────────────
  // The hook owns persistence + availability fallback; see shared.tsx.
  const {
    setViewMode: setViewModePersist, effectiveMode,
    weeklyAvailable, monthlyAvailable,
  } = useSeasonalViewMode(VIEWMODE_KEY, refIndex, range)

  // Per-carrier seasonal view: bucket each carrier's balance series by
  // hour-of-week / hour-of-month and average across the filter range. One
  // entry per (carrier, season, bucket) → drives the per-carrier
  // 4-quadrant grids rendered below.
  //
  // Multi-period note: input series carry every (period, timestep) row,
  // so a multi-period network averages the same hour-of-week across ALL
  // periods — a representative profile spanning the whole horizon. Drill
  // into a specific period via the Period strip for single-period detail.
  // Result shape: per (busCarrier, season) → list of bucket rows. Each row
  // carries:
  //   • One field per gen carrier present (`Gen:gas`, `Gen:solar`, …) for the
  //     stacked production area (replaces the legacy single `Generation` key).
  //   • Storage / Stores / Conversion in/out split into `▲` (positive) and
  //     `▼` (negative) variants — unchanged from the previous shape.
  //   • `Load` as a flat scalar — rendered as the dashed line on top.
  //
  // `genCarriersByBusCarrier` tracks which gen-carrier columns are non-empty
  // for each bus carrier so the section renderer knows exactly which Areas
  // to emit (skipping zero-carrier traces keeps the legend clean).
  const seasonalViewByCarrier = useMemo(() => {
    if (effectiveMode === 'timeframe') return null
    const mode = effectiveMode
    const nBuckets = mode === 'weekly' ? 7 * 24 : 31 * 24
    const out = new Map<string, {
      seasons: Record<Season, Array<Record<string, number>>>
      genCarriers: string[]   // sorted list of gen carriers with non-zero data
    }>()
    for (const c of availableCarriers) {
      const carrierBalance = seriesByCarrier.get(c)
      if (!carrierBalance) continue
      const byGC = carrierBalance.generationByGenCarrier
      const genCarrierList = byGC ? [...byGC.keys()] : []
      const seasons: Record<Season, Array<Record<string, number>>> = {
        Winter: [], Spring: [], Summer: [], Autumn: [],
      }
      // Track which gen carriers have ANY non-zero value across all seasons —
      // pruning zero-only ones keeps the chart legend short.
      const nonEmptyGenCarriers = new Set<string>()
      for (const season of SEASONS) {
        // Non-generation groups (Storage / Stores / Conversion / Load).
        const sums: Record<string, Float64Array> = {}
        const counts: Record<string, Int32Array> = {}
        for (const gk of CARRIER_GROUPS) {
          if (gk === 'Generation') continue  // handled per-gen-carrier below
          sums[gk] = new Float64Array(nBuckets)
          counts[gk] = new Int32Array(nBuckets)
        }
        for (const gk of CARRIER_GROUPS) {
          if (gk === 'Generation') continue
          const gs = carrierBalance[gk]
          if (!gs) continue
          for (const { snapshot, value } of gs) {
            const p = parseSeasonStamp(snapshot)
            if (!p || SEASON_OF_MONTH[p.month] !== season) continue
            if (!Number.isFinite(value)) continue
            const b = mode === 'weekly'
              ? p.dow * 24 + p.hour
              : (p.dayOfMonth - 1) * 24 + p.hour
            if (b < 0 || b >= nBuckets) continue
            sums[gk][b] += value
            counts[gk][b] += 1
          }
        }
        // Per-gen-carrier generation buckets.
        const genSums: Record<string, Float64Array> = {}
        const genCounts: Record<string, Int32Array> = {}
        for (const gc of genCarrierList) {
          genSums[gc] = new Float64Array(nBuckets)
          genCounts[gc] = new Int32Array(nBuckets)
          const gs = byGC!.get(gc)
          if (!gs) continue
          for (const { snapshot, value } of gs) {
            const p = parseSeasonStamp(snapshot)
            if (!p || SEASON_OF_MONTH[p.month] !== season) continue
            if (!Number.isFinite(value)) continue
            const b = mode === 'weekly'
              ? p.dow * 24 + p.hour
              : (p.dayOfMonth - 1) * 24 + p.hour
            if (b < 0 || b >= nBuckets) continue
            genSums[gc][b] += value
            genCounts[gc][b] += 1
          }
        }
        let lastNonEmpty = -1
        const rows: Array<Record<string, number>> = []
        for (let b = 0; b < nBuckets; b++) {
          const row: Record<string, number> = { bucket: b }
          let any = false
          // Gen-carrier columns — keyed `Gen:<carrier>` to avoid colliding
          // with the Storage / Stores / Conversion / Load keys.
          for (const gc of genCarrierList) {
            if (genCounts[gc][b] === 0) continue
            const avg = genSums[gc][b] / genCounts[gc][b]
            if (Math.abs(avg) > 0) {
              row[`Gen:${gc}`] = avg
              nonEmptyGenCarriers.add(gc)
              any = true
            }
          }
          // Non-generation columns.
          for (const gk of CARRIER_GROUPS) {
            if (gk === 'Generation') continue
            if (counts[gk][b] === 0) continue
            const avg = sums[gk][b] / counts[gk][b]
            if (CARRIER_SPLIT_GROUPS.includes(gk)) {
              row[`${gk} ▲`] = Math.max(avg, 0)
              row[`${gk} ▼`] = Math.min(avg, 0)
            } else {
              row[gk] = avg
            }
            any = true
          }
          rows.push(row)
          if (any) lastNonEmpty = b
        }
        seasons[season] = rows.slice(0, lastNonEmpty + 1)
      }
      // Sort gen carriers: thermal first (base of stack), then renewables —
      // matches the horizon-view DispatchStack convention.
      const genCarriers = [...nonEmptyGenCarriers].sort((a, b) => {
        const ar = isRenewableCarrier(a) ? 1 : 0
        const br = isRenewableCarrier(b) ? 1 : 0
        if (ar !== br) return ar - br
        return a.localeCompare(b)
      })
      out.set(c, { seasons, genCarriers })
    }
    return out
  }, [effectiveMode, availableCarriers, seriesByCarrier])

  const hasResults = gensTS != null || storTS != null || storPowerTS != null
                  || storeTS != null || storeETS != null || loadTS != null

  // Inner-view switcher: 'dispatch' keeps the existing time-series + KPI
  // surface; 'flh' opens a per-asset Full Load Hours evaluation table.
  // Persisted in component state only (cheap) — survives unmount/remount
  // because React Query holds the underlying data anyway.
  const [view, setView] = useState<'dispatch' | 'flh'>('dispatch')

  if (!hasResults) {
    return (
      <div className="p-6 text-center text-xs text-muted">
        No solved network. Run a simulation (header → <span className="font-medium text-text">Run LOPF</span>) to populate dispatch results.
      </div>
    )
  }

  // `[&>*]:shrink-0` — this is a scroll container (overflow-y-auto). Without
  // it, flexbox compresses each child section to fit the viewport; sections
  // with `overflow-hidden` then clip their charts to a thin sliver.
  return (
    <div className="flex flex-col gap-5 p-5 overflow-y-auto h-full text-sm [&>*]:shrink-0">

      {/* ── View switcher: Dispatch ↔ Full Load Hours ─────────── */}
      <section className="flex items-center gap-2 flex-wrap">
        <Seg
          value={view}
          onChange={setView}
          options={[
            { value: 'dispatch', label: 'Dispatch',
              title: 'Time-series dispatch view (KPIs, per-group charts, heatmaps).' },
            { value: 'flh', label: 'Full load hours',
              title: 'Per-asset full-load-hours evaluation (FLH = Σ p × Δt / p_nom_opt).' },
          ]}
        />
        <span className="text-[10px] text-muted">
          {view === 'dispatch'
            ? 'Time-series dispatch with weekly / monthly / full-timeframe drilldowns.'
            : 'Per-asset full-load-hours table — Σ weighted dispatch divided by installed capacity.'}
        </span>
      </section>

      {view === 'flh' ? (
        <FullLoadHoursSection
          generators={generators as Generator[]}
          storageUnits={storageUnits as StorageUnit[]}
          links={links as LinkT[]}
          gensTS={gensTS as TSPayload | null}
          storPowerTS={storPowerTS as TSPayload | null}
          linkTS={linkTS as TSPayload | null}
          weightCtx={weightCtx}
          range={range}
          selectedPeriod={filter.selectedPeriod}
          electrolyzerNames={electrolyzerNames}
          vintageResults={vintageResults}
          scaled={scaled}
          avgScale={avgScale}
        />
      ) : (
      <>

      {/* ── Aggregated System dispatch + OPEX breakdown REMOVED ────────
          User chose to replace the monolithic aggregated overview with the
          per-carrier KPI panels (CarrierKpiPanel) rendered alongside each
          per-carrier DispatchStack below. The aggregated numbers are still
          accessible by summing the per-carrier panels; for a single-number
          system total see /api/results/cost_breakdown. The Top-profit chart
          and per-asset table further down handle cross-carrier asset views. */}
      <section style={{display: 'none'}}>
        <h3 className="text-[12.5px] font-semibold text-text mb-2.5 flex items-center gap-2 tracking-[-0.005em]">
          System dispatch
          {scaled && (
            <span className="text-[10px] font-normal text-accent bg-accent/10 px-1.5 py-0.5 rounded font-mono"
              title={`Energy totals scaled by snapshot_weightings.generators × investment_period_weightings.years. Average factor across the selected horizon: ${avgScale.toFixed(2)}.`}>
              × {avgScale.toFixed(2)} weighted
            </span>
          )}
        </h3>
        <div className="grid grid-cols-3 gap-3">
          {/* Total demand = sum over ALL load carriers. On multi-carrier
              networks (electrical + H2 + heat) this is a mixed-unit
              quantity, so the per-carrier KPIs below break it down into
              comparable buckets. Each gets its own KPI tile only when
              the corresponding bucket has demand > 0. */}
          <KPI label="Total demand"     value={fmtEnergy(kpis.load)}     hint={'Σ p_set × snapshot_weightings × period years over ALL load carriers (electrical + H2 + heat + …)' + scaleHint} />
          {Object.entries(kpis.loadByCarrier)
            .filter(([, v]) => v != null && Math.abs(v) > 0)
            .sort(([a], [b]) => loadCarrierSortKey(a).localeCompare(loadCarrierSortKey(b)))
            .map(([carrier, v]) => (
              <KPI
                key={`demand-${carrier}`}
                label={`Demand · ${loadCarrierLabel(carrier)}`}
                value={fmtEnergy(v)}
                hint={`Σ p_set × weightings over loads with carrier "${carrier}"` + scaleHint}
              />
            ))}
          <KPI label="Thermal"          value={fmtEnergy(kpis.thermal)}  hint={'Σ p_t × weighting over thermal generators' + scaleHint} />
          <KPI label="Renewables"       value={fmtEnergy(kpis.renewable)} hint={'Σ p_t × weighting over renewable generators' + scaleHint} />
          <KPI label="Total generation" value={fmtEnergy(kpis.totalGen)} hint={'Thermal + Renewables (weighted)' + scaleHint} />
          <KPI label="Storage discharged"
               value={fmtEnergy(kpis.storageDischarge)}
               hint={'energy released by storage units (production)' + scaleHint} />
          <KPI label="Storage charged"
               value={fmtEnergy(kpis.storageCharge)}
               hint={'energy absorbed by storage units (consumption)' + scaleHint} />
          {/* Single-period default has avgScale=1 and `scaled` is false, so
              the hints just show the formula description — no surprise added
              text to long-time users on flat-snapshot runs. */}
          {/* Renewable curtailment summary. Energy MWh + share of total
              renewable potential (delivered + curtailed). Only shown when
              the backend returned data — empty when the run wasn't solved
              or the network has no time-varying p_max_pu. */}
          <KPI label="Renewable curtailment"
               value={kpis.curtailEnergy != null
                 ? `${fmtEnergy(kpis.curtailEnergy)}${kpis.curtailPct != null ? ` · ${kpis.curtailPct.toFixed(1)} %` : ''}`
                 : '—'}
               hint={'Σ max(p_max_pu × p_nom − p, 0) over renewable generators' + scaleHint} />
          {/* Lost load — populated only when last LOPF ran with voll > 0
              AND the LP actually shed demand. KPI shows energy + cost so
              users can read the reliability gap at a glance. */}
          <KPI label="Lost load"
               value={lostLoadTotals ? fmtEnergy(lostLoadTotals.mwh) : '—'}
               hint={"Demand the LP couldn't serve across ALL energy carriers (electrical + H2 + heat + …) — absorbed by VOLL slack generators on every bus. Set Solver Settings → VOLL > 0 to enable." + scaleHint} />
          <KPI label="Lost-load cost"
               value={lostLoadTotals ? fmtCurrency(lostLoadTotals.cost) : '—'}
               hint={'MWh of lost load × the VOLL set in Solver Settings (e.g. 3000–10000 €/MWh). Summed across all carriers.' + scaleHint} />
          {/* Per-carrier lost-load split. Only renders when the backend
              returned bus_carriers AND at least one non-electrical
              carrier shed load — for purely electrical networks this is
              identical to the total above and the row would be noise. */}
          {lostLoadByCarrier && lostLoadByCarrier.length > 1 && lostLoadByCarrier.map(b => (
            <KPI
              key={`lostload-${b.carrier}`}
              label={`Lost load · ${loadCarrierLabel(b.carrier)}`}
              value={fmtEnergy(b.mwh)}
              hint={`VOLL slack dispatched at ${loadCarrierLabel(b.carrier).toLowerCase()} buses — ${fmtCurrency(b.cost)} at the configured VOLL price.` + scaleHint}
            />
          ))}
        </div>
      </section>

      {/* ── OPEX breakdown REMOVED (replaced by per-carrier panels) ──── */}
      {cost && (() => {
        // Resolve which slice of the cost data to render.
        const periodEntry = (filter.selectedPeriod != null && cost.by_period)
          ? cost.by_period.find(p => p.period === filter.selectedPeriod)
          : undefined
        const dispatchOpex = periodEntry?.opex ?? cost.opex
        const byComponent = periodEntry?.by_component
          ?? cost.by_component.map(b => ({ component: b.component, capex: b.capex, opex: b.opex }))
        const scopeLabel = periodEntry
          ? `period ${filter.selectedPeriod}`
          : 'horizon (all periods)'
        return (
        <section style={{display: 'none'}}>
          <div className="flex items-center justify-between mb-2.5">
            <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">
              Operational expenditure (OPEX)
              <span className="ml-2 text-[10px] font-normal text-muted">{scopeLabel}</span>
            </h3>
            <button
              onClick={() => {
                const rows = byComponent
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
            <KPI label="Total OPEX"
              value={fmtCurrency(dispatchOpex + curtailmentCost)}
              hint={`Dispatch OPEX (${fmtCurrency(dispatchOpex, 1)}) + curtailment penalty (${fmtCurrency(curtailmentCost, 1)}). Matches the OPEX-side total the LP minimised for ${scopeLabel}.`} />
            <KPI label="Dispatch OPEX" value={fmtCurrency(dispatchOpex)}
              hint={`Σ marginal_cost × dispatch over ${scopeLabel} (PyPSA n.statistics() 'Operational Expenditure', already weighted by snapshot_weightings × period years).`} />
            <KPI label="Curtailment penalty"
              value={curtailmentCost > 0 ? fmtCurrency(curtailmentCost) : '—'}
              hint={`Σ curtailment_t × curtailment_cost over ${scopeLabel}. Set curtailment_cost > 0 on a renewable to start charging the optimiser for spilled energy.`} />
            {byComponent
              .filter(b => b.opex > 0)
              .sort((a, b) => b.opex - a.opex)
              .map(b => (
                <KPI key={b.component} label={b.component} value={fmtCurrency(b.opex)} />
              ))
            }
          </div>
          {/* Per-carrier OPEX rollup. Respects the period selector — when
              the user has picked a specific period, we use the period's
              `by_carrier` slice from `cost.by_period` so the numbers match
              the per-component KPIs above (which are also period-scoped).
              On "Aggregated" we fall back to the horizon-wide `cost.by_carrier`. */}
          {(() => {
            const carrierOpex = new Map<string, number>()
            // Pick the source: period-scoped if a period is selected,
            // horizon-wide otherwise.
            const periodCarrierRows = periodEntry?.by_carrier ?? null
            if (periodCarrierRows) {
              for (const row of periodCarrierRows) {
                if (!row.opex || row.opex <= 0) continue
                const key = (row.carrier || 'unspecified').toLowerCase()
                carrierOpex.set(key, (carrierOpex.get(key) ?? 0) + row.opex)
              }
            } else {
              for (const row of cost.by_carrier ?? []) {
                if (!row.opex || row.opex <= 0) continue
                const key = (row.carrier || 'unspecified').toLowerCase()
                carrierOpex.set(key, (carrierOpex.get(key) ?? 0) + row.opex)
              }
            }
            if (carrierOpex.size === 0) return null
            const entries = Array.from(carrierOpex.entries())
              .sort(([, a], [, b]) => b - a)
            const sourceLabel = periodCarrierRows
              ? `period ${filter.selectedPeriod}`
              : 'horizon (all periods)'
            return (
              <div className="mt-3">
                <h4 className="text-[11px] font-semibold text-muted tracking-[-0.005em] mb-2">
                  OPEX by carrier
                  <span className="ml-2 text-[10px] font-normal text-muted">
                    {sourceLabel} · sums per-component rows so a single carrier (gas, electrolyser, h2 pipeline) covering multiple components rolls up cleanly
                  </span>
                </h4>
                <div className="grid grid-cols-4 gap-3">
                  {entries.map(([c, v]) => (
                    <KPI
                      key={`opex-carrier-${c}`}
                      label={`OPEX · ${c}`}
                      value={fmtCurrency(v)}
                      hint={`Σ Operational Expenditure over all components with carrier "${c}" for ${sourceLabel}.` + scaleHint}
                    />
                  ))}
                </div>
              </div>
            )
          })()}
        </section>
        )
      })()}

      {/* ── Dispatch view controls: mode + season + carriers ───────── */}
      {/* Three controls sit on one row so the user can configure the
          seasonal view without jumping between sections:
          - Mode (Weekly / Monthly / Full timeframe): bucketing granularity
          - Season (All / Winter / Spring / Summer / Autumn): zoom into one
            quadrant or keep the default 4-grid
          - Carriers (Electricity / H₂ / Heat / …): which bus carriers get
            their own seasonal stack. Defaults to all available so the
            multi-carrier picture is the default behaviour. */}
      <section className="flex items-center gap-3 flex-wrap">
        <span className="text-[11px] font-medium text-text">Dispatch view</span>
        <Seg
          value={effectiveMode}
          onChange={setViewModePersist}
          options={[
            { value: 'weekly', label: 'Weekly', disabled: !weeklyAvailable,
              title: weeklyAvailable
                ? 'Averaged week per season (Mon–Sun, 168 h)'
                : `Greyed out — needs ≥ ${WEEKLY_MIN_DAYS} days of data in all 4 seasons` },
            { value: 'monthly', label: 'Monthly', disabled: !monthlyAvailable,
              title: monthlyAvailable
                ? 'Averaged month per season (day-of-month × hour)'
                : `Greyed out — needs ≥ ${MONTHLY_MIN_DAYS} days of data in all 4 seasons` },
            { value: 'timeframe', label: 'Full timeframe',
              title: 'The full dataset — per-group dispatch charts' },
          ]}
        />
        {effectiveMode !== 'timeframe' && (
          <>
            <span className="text-[11px] text-muted">Season:</span>
            <select
              value={selectedSeason}
              onChange={(e) => setSelectedSeason(e.target.value as SelectableSeason)}
              className="text-[11px] rounded border border-border bg-bg-2 px-1.5 py-0.5 text-text"
              title="Restrict the seasonal grid to a single season (zoom) or show all four."
            >
              <option value="all">All seasons</option>
              {SEASONS.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </>
        )}
        {availableCarriers.length > 1 && (
          <div className="flex items-center gap-1 flex-wrap">
            <span className="text-[11px] text-muted">Carriers:</span>
            {availableCarriers.map(c => {
              const sel = selectedCarriers.has(c)
              return (
                <button
                  key={c}
                  type="button"
                  onClick={() => {
                    const next = new Set(selectedCarriers)
                    if (next.has(c)) {
                      // Keep at least one carrier selected — empty selection
                      // would render a blank panel which is confusing.
                      if (next.size > 1) next.delete(c)
                    } else { next.add(c) }
                    setSelectedCarriers(next)
                  }}
                  className={`text-[11px] rounded-full border px-2 py-0.5 transition-colors ${
                    sel
                      ? 'border-accent bg-accent/10 text-accent font-medium'
                      : 'border-border bg-bg-2 text-muted hover:text-text'
                  }`}
                  title={sel ? `Hide ${carrierDisplayLabel(c)} seasonal stack` : `Show ${carrierDisplayLabel(c)} seasonal stack`}
                >
                  {carrierDisplayLabel(c)}
                </button>
              )
            })}
          </div>
        )}
      </section>

      {/* ── Load- & residual-load duration curves ─────────────────── */}
      {durationCurves && durationCurves.rows.length > 1 && (
        <ChartCard
          title="Load duration curve"
          hint={durationCurves.hasVre ? 'total load vs residual (load − renewables), each sorted descending'
                                       : 'total load, sorted descending'}
          right={
            <ChartActions
              chartRef={durationChartRef}
              onExportCSV={() => {
                downloadCSV('load_duration_curve.csv',
                  ['pct_of_hours', 'load_MW', 'residual_load_MW'],
                  durationCurves.rows.map(r => [r.pct, r.load, r.residual]))
                toast.success('Exported')
              }}
              filename="load_duration_curve" />
          }
          bodyClassName="p-3"
        >
          <p className="text-[11px] text-muted mb-2">
            Total system load sorted high-to-low (x = % of hours). {durationCurves.hasVre && (
              <>The <span className="font-medium text-text">residual</span> curve subtracts variable-renewable
              generation — the net demand the dispatchable + storage fleet must meet (its peak sizes firm
              capacity; a dip below zero flags renewable-surplus hours).</>
            )}
          </p>
          <div ref={durationChartRef}>
            <ResponsiveContainer width="100%" height={200}>
              <AreaChart data={durationCurves.rows} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="ldc-load-grad" x1="0" x2="0" y1="0" y2="1">
                    <stop offset="0%" stopColor="#6b7280" stopOpacity={0.22} />
                    <stop offset="100%" stopColor="#6b7280" stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="ldc-res-grad" x1="0" x2="0" y1="0" y2="1">
                    <stop offset="0%" stopColor="#0369a1" stopOpacity={0.28} />
                    <stop offset="100%" stopColor="#0369a1" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid {...CHART_GRID} />
                <XAxis dataKey="pct" type="number" domain={[0, 100]}
                  tickFormatter={(v: number) => `${v.toFixed(0)} %`} {...CHART_AXIS} />
                <YAxis {...CHART_AXIS} tickFormatter={(v: number) => fmtPower(v, 1)} label={yAxisLabel('MW')} />
                <RTooltip {...CHART_TOOLTIP}
                  labelFormatter={(v: number) => `${Number(v).toFixed(1)} % of hours`}
                  formatter={(v: number, n: string) => [fmtPower(v), n === 'residual' ? 'Residual load' : 'Load']} />
                {durationCurves.hasVre && <Legend {...CHART_LEGEND} />}
                <Area type="monotone" dataKey="load" name="Load" stroke="#6b7280"
                      fill="url(#ldc-load-grad)" strokeWidth={1.4} isAnimationActive={false} />
                {durationCurves.hasVre && (
                  <Area type="monotone" dataKey="residual" name="Residual load" stroke="#0369a1"
                        fill="url(#ldc-res-grad)" strokeWidth={1.6} isAnimationActive={false} />
                )}
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>
      )}

      {effectiveMode !== 'timeframe' && seasonalViewByCarrier ? (
        <div className="flex flex-col gap-4">
          {availableCarriers
            .filter(c => selectedCarriers.has(c))
            .map(c => {
              const view = seasonalViewByCarrier.get(c)
              if (!view) return null
              // Map canonical carrier (`c`) back to the matching
              // `busCarrierGroups` entries — there can be more than one when a
              // network mixes spellings (e.g. one bus tagged `AC` and another
              // tagged `electricity`, both canonicalising to 'electricity').
              // Merge their gens/loads/storage/busNames into a single group so
              // the KPI panel sees the full picture for the carrier.
              const groupForKpi = (() => {
                const matches = busCarrierGroups.filter(
                  g => canonicalCarrier(g.carrier) === c,
                )
                if (matches.length === 0) return null
                if (matches.length === 1) return matches[0]
                const gens = new Set<string>()
                const loadsSet = new Set<string>()
                const storage = new Set<string>()
                const storesSet = new Set<string>()
                const busNames = new Set<string>()
                for (const m of matches) {
                  m.gens.forEach(x => gens.add(x))
                  m.loads.forEach(x => loadsSet.add(x))
                  m.storage.forEach(x => storage.add(x))
                  m.stores.forEach(x => storesSet.add(x))
                  m.busNames.forEach(x => busNames.add(x))
                }
                return { carrier: c, gens, loads: loadsSet, storage, stores: storesSet, busNames }
              })()
              return (
                <div key={c} className="flex flex-col gap-2">
                  {groupForKpi && (
                    <CarrierKpiPanel
                      carrier={groupForKpi.carrier}
                      group={groupForKpi}
                      gensTS={gensTS as TSPayload | null}
                      loadTS={loadTS as TSPayload | null}
                      storPowerTS={storPowerTS as TSPayload | null}
                      storeTS={storeTS as TSPayload | null}
                      curtailTS={curtailTS as TSPayload | null}
                      linkTS={linkTS as TSPayload | null}
                      generators={generators as Generator[]}
                      loads={loads as Load[]}
                      storageUnits={storageUnits as StorageUnit[]}
                      stores={stores as Store[]}
                      links={links as LinkT[]}
                      weightCtx={weightCtx}
                      range={range}
                      lostLoadByCarrier={lostLoadByCarrier}
                      econByCarrier={econByCarrier?.by_carrier ?? null}
                      scaled={scaled}
                      avgScale={avgScale}
                      isElectrical={c === 'electricity'}
                      selectedPeriod={filter.selectedPeriod}
                    />
                  )}
                  <CarrierSeasonalSection
                    carrier={c}
                    mode={effectiveMode}
                    data={view}
                    season={selectedSeason}
                  />
                </div>
              )
            })}
        </div>
      ) : (
        <>
      {/* ── Per-bus-carrier KPI panels + dispatch stacks ─────── */}
      {/* Renders in BOTH aggregated and individual-period horizon modes — the
          KPIs honour `range`, which collapses to a single period when the
          user picks one in the Period strip, or spans the whole horizon when
          "Aggregated" is active. Earlier this whole block was gated on
          `selectedPeriod != null`, which hid the KPI surface entirely in
          aggregated mode (the user had to drill into a single period to see
          economics). */}
      <>
          {/* Per-bus-carrier dispatch stacks. One chart per distinct bus
              carrier (AC / heat / H2 / …) so a heat-pump's heat output
              isn't conflated with an electrical generator's MW on the same
              axis. The electrical-bus chart still includes the electrolyser
              consume series because that link sinks electricity at its
              bus0 (an electrical bus); the heat / H2 stacks omit it.
              Falls back to the single-stack legacy layout when no buses
              are loaded yet (mid-mount) or the network has only one
              bus-carrier present (typical electricity-only project). */}
          {busCarrierGroups.length <= 1 ? (
            <DispatchStack
              gensTS={gensTS as TSPayload | null}
              loadTS={loadTS as TSPayload | null}
              storPowerTS={storPowerTS as TSPayload | null}
              generators={generators as Generator[]}
              loads={loads as Load[]}
              storageUnits={storageUnits as StorageUnit[]}
              links={links as LinkT[]}
              linkTS={linkTS as TSPayload | null}
              electrolyzerNames={electrolyzerNames}
              electricalLoadNames={loadGroups.find(g => g.carrier === 'electrical')?.nameSet}
              electricalStorageNames={electricalStorageNames}
              range={range}
            />
          ) : busCarrierGroups.map(group => {
            // Skip stacks where the carrier has zero relevant components AND
            // no link touches this carrier's buses. Without checking links a
            // pure transit carrier (heat-pump producing on a heat bus that has
            // no native generators/loads/storage on disk) is hidden, even
            // though the LP genuinely dispatches heat there.
            const carrierHasLinkTouch = (links as LinkT[]).some(l =>
              (l.bus0 && group.busNames.has(l.bus0)) ||
              (l.bus1 && group.busNames.has(l.bus1)) ||
              ((l as { bus2?: string }).bus2 && group.busNames.has((l as { bus2?: string }).bus2!))
            )
            if (group.gens.size === 0 && group.loads.size === 0 && group.storage.size === 0 && !carrierHasLinkTouch) return null
            // group.carrier is already lowercased in busCarrierGroups, so a
            // bare-set match is enough — covers the three common spellings
            // PyPSA / PyPSA-Eur projects use interchangeably.
            const isElectrical = ['ac', 'electricity', 'electrical'].includes(group.carrier)
            return (
              <div key={group.carrier} className="flex flex-col gap-2">
                <CarrierKpiPanel
                  carrier={group.carrier}
                  group={group}
                  gensTS={gensTS as TSPayload | null}
                  loadTS={loadTS as TSPayload | null}
                  storPowerTS={storPowerTS as TSPayload | null}
                  storeTS={storeTS as TSPayload | null}
                  curtailTS={curtailTS as TSPayload | null}
                  linkTS={linkTS as TSPayload | null}
                  generators={generators as Generator[]}
                  loads={loads as Load[]}
                  storageUnits={storageUnits as StorageUnit[]}
                  stores={stores as Store[]}
                  links={links as LinkT[]}
                  weightCtx={weightCtx}
                  range={range}
                  lostLoadByCarrier={lostLoadByCarrier}
                  econByCarrier={econByCarrier?.by_carrier ?? null}
                  scaled={scaled}
                  avgScale={avgScale}
                  isElectrical={isElectrical}
                  selectedPeriod={filter.selectedPeriod}
                />
                <DispatchStack
                  gensTS={gensTS as TSPayload | null}
                  loadTS={loadTS as TSPayload | null}
                  storPowerTS={storPowerTS as TSPayload | null}
                  generators={generators as Generator[]}
                  loads={loads as Load[]}
                  storageUnits={storageUnits as StorageUnit[]}
                  links={links as LinkT[]}
                  linkTS={linkTS as TSPayload | null}
                  // Electrolyser consume only belongs on the electrical bus —
                  // bus0 of an electrolyser link is electricity. Omitting it
                  // on the H2 / heat stacks avoids a phantom "consume" series.
                  electrolyzerNames={isElectrical ? electrolyzerNames : EMPTY_STRING_SET}
                  electricalLoadNames={group.loads}
                  electricalStorageNames={group.storage}
                  busNames={group.busNames}
                  genNames={group.gens}
                  carrierLabel={group.carrier}
                  range={range}
                />
              </div>
            )
          })}
          {/* Per-carrier storage heatmaps — kept gated on `selectedPeriod` so
              the heatmap grid (1 row per day, 24 cells per row) doesn't
              balloon to thousands of rows in aggregated horizon view. */}
          {filter.selectedPeriod != null && (
          <>
          {/* Per-carrier storage heatmaps. The previous single chart summed
              MW across every storage technology (battery + H2 + heat) which
              on a sector-coupled network produced a meaningless mix. Now
              each storage carrier gets its own (day × hour) grid, scoped
              via columnFilter to that carrier's asset names. Renders nothing
              when the network has zero storage units; renders ONE grid
              when there's a single carrier (no carrier suffix in the title)
              for visual parity with the original single-storage look. */}
          {storageGroups.length === 1 && (
            <StorageHeatmap
              storPowerTS={storPowerTS as TSPayload | null}
              range={range}
              columnFilter={storageGroups[0].nameSet}
            />
          )}
          {storageGroups.length > 1 && storageGroups.map(g => (
            <StorageHeatmap
              key={g.carrier}
              storPowerTS={storPowerTS as TSPayload | null}
              range={range}
              columnFilter={g.nameSet}
              title={`${storageCarrierLabel(g.carrier)} storage — daily charge / discharge pattern`}
              subtitle={`1 row per day, 24 cells per row · red = discharge, blue = charge · ${g.assets.length} unit${g.assets.length === 1 ? '' : 's'}`}
              csvName={`storage_heatmap_${g.carrier}.csv`}
            />
          ))}
          {/* Electrolyzer heatmap — same grid as storage, but filtered to
              electrolyser links and rendered with a single-hue blue
              gradient (PyPSA Link p0 is positive when consuming electricity
              at bus0, so values are one-directional and the diverging
              palette would always read as "discharge" red). */}
          {electrolyzerNames.size > 0 && (
            <StorageHeatmap
              storPowerTS={linkTS as TSPayload | null}
              range={range}
              columnFilter={electrolyzerNames}
              mode="unsigned-blue"
              title="Electrolyzer heatmap — daily consumption pattern"
              subtitle="1 row per day, 24 cells per row · darker blue = more H₂ produced"
              csvName="electrolyzer_heatmap.csv"
              legendLabels={{ negative: 'Idle', positive: 'Consume' }}
            />
          )}
          </>
          )}
      </>

      {/* ── Per-group sections ────────────────────────────────── */}
      {/* Load moved to the top of the section list to match the new KPI order
          (load-first reading flow). Generation groups follow. Lost-load is
          integrated as a toggle inside this section (same pattern as
          curtailment-under-renewables) so users find unmet-demand info where
          they already look for load data. */}
      {/* Load — single section with a carrier picker at the top so the
          user can switch between Electrical / Hydrogen / Heat without
          stacking three sections vertically. Lost-load is only meaningful
          for electrical demand so we only attach the lost-load overlay
          when the active carrier is electrical — non-electrical demand
          can't be shed by the LP's VOLL slack mechanism. */}
      {loadSeriesByCarrier.length === 0 ? (
        <GroupSection
          groupKey="Load" assets={loads as Load[]}
          nameField="name" carrierField="carrier"
          agg={series.Load} ts={loadTS as TSPayload | null} memberSet={loadNames}
          lostLoadSeries={lostLoadSeries}
          lostLoadTotals={lostLoadTotals}
          rowRange={range}
        />
      ) : (
        <CarrierTabbedSection
          carriers={loadSeriesByCarrier.map(g => ({
            id: g.carrier,
            label: loadCarrierLabel(g.carrier),
            assets: g.assets,
            agg: g.agg,
            nameSet: g.nameSet,
          }))}
          groupKey="Load"
          ts={loadTS as TSPayload | null}
          rowRange={range}
          extraPropsForCarrier={(carrierId) => ({
            lostLoadSeries: loadIsElectrical(carrierId) ? lostLoadSeries : undefined,
            lostLoadTotals: loadIsElectrical(carrierId) ? lostLoadTotals : null,
          })}
        />
      )}
      <GroupSection
        groupKey="Thermal" assets={(generators as Generator[]).filter(g => groupCols.thermal.has(g.name))}
        nameField="name" carrierField="carrier"
        agg={series.Thermal} ts={gensTS as TSPayload | null} memberSet={groupCols.thermal}
        rowRange={range}
      />
      <GroupSection
        groupKey="Renewables" assets={(generators as Generator[]).filter(g => groupCols.renew.has(g.name))}
        nameField="name" carrierField="carrier"
        agg={series.Renewables} ts={gensTS as TSPayload | null} memberSet={groupCols.renew}
        curtailmentSeries={curtailmentSeries}
        rowRange={range}
      />
      {/* Storage — one pair of sections per carrier (Battery, Hydrogen,
          Heat, Pumped Hydro, …). Each carrier gets its own MW power-flow
          chart AND its own MWh state-of-charge chart so the user can read
          electrical battery cycling separately from H2 / heat reservoirs
          (which behave very differently — H2 typically charges over weeks,
          batteries cycle daily). */}
      {storageSeriesByCarrier.length === 0 ? (
        <GroupSection
          groupKey="Storage" assets={storageUnits as StorageUnit[]}
          nameField="name" carrierField="carrier"
          agg={series.Storage} ts={storPowerTS as TSPayload | null} memberSet={storageNames}
          unitOverride="MW signed: + discharge / − charge"
          rowRange={range}
        />
      ) : storageSeriesByCarrier.map(group => (
        <Fragment key={`storage-${group.carrier}`}>
          <GroupSection
            groupKey="Storage"
            titleOverride={`Storage · ${storageCarrierLabel(group.carrier)}`}
            assets={group.assets}
            nameField="name" carrierField="carrier"
            agg={group.aggPower}
            ts={storPowerTS as TSPayload | null}
            memberSet={group.nameSet}
            unitOverride="MW signed: + discharge / − charge"
            rowRange={range}
          />
          <GroupSection
            groupKey="Storage SoC"
            titleOverride={`Storage SoC · ${storageCarrierLabel(group.carrier)}`}
            assets={group.assets}
            nameField="name" carrierField="carrier"
            agg={group.aggSoC}
            ts={storTS as TSPayload | null}
            memberSet={group.nameSet}
            unitOverride="MWh state of charge"
            vintageResults={vintageResults}
            snapshotPeriods={(storTS as TSPayload | null)?.periods}
            rowRange={range}
          />
        </Fragment>
      ))}
      {/* Stores power flow (signed MW): same convention as Storage. */}
      <GroupSection
        groupKey="Stores" assets={stores as Store[]}
        nameField="name" carrierField="carrier"
        agg={series.Stores} ts={storeTS as TSPayload | null} memberSet={storeNames}
        unitOverride="MW signed: + discharge / − charge"
        rowRange={range}
      />
      {/* Stores energy: the equivalent of SoC for the Store component. */}
      <GroupSection
        groupKey="Stores Energy" assets={stores as Store[]}
        nameField="name" carrierField="carrier"
        agg={series['Stores Energy']} ts={storeETS as TSPayload | null} memberSet={storeNames}
        unitOverride="MWh stored"
        vintageResults={vintageResults}
        snapshotPeriods={(storeETS as TSPayload | null)?.periods}
        rowRange={range}
      />
      {/* Links — one section per carrier so HVDC / electrolyser / fuel-cell
          / pipeline flows are visually separated. PyPSA Links are signed at
          bus0 (positive = bus0 → bus1, the "forward" direction), same MW
          convention as Storage power above. Empty groups (no links of that
          carrier exist on the network) are skipped; if the network carries
          NO links at all, fall back to a single placeholder section. */}
      {linkSeriesByCarrier.length === 0 ? (
        <GroupSection
          groupKey="Links" assets={links as LinkT[]}
          nameField="name" carrierField="carrier"
          agg={null} ts={null} memberSet={linkNames}
          emptyHint="No links defined on this network. Add HVDC, electrolyser, fuel-cell, or pipeline links via the canvas to see per-link power flows here."
        />
      ) : linkSeriesByCarrier.map(group => (
        <GroupSection
          key={`Links-${group.carrier}`}
          groupKey="Links"
          titleOverride={`Links · ${group.carrier}`}
          assets={group.assets}
          nameField="name" carrierField="carrier"
          agg={group.agg}
          ts={linkTS as TSPayload | null}
          memberSet={group.nameSet}
          unitOverride="MW signed: + bus0 → bus1 / − bus1 → bus0"
          rowRange={range}
        />
      ))}
        </>
      )}
      </>
      )}
    </div>
  )
}

// ── Carrier-specific seasonal section ────────────────────────────────────────
// One 4-quadrant (Winter/Spring/Summer/Autumn) seasonal grid for a SINGLE
// bus carrier — Electricity / H₂ / Heat / … . Each season chart plots the
// balance at that carrier's buses: per-fuel Generation areas stacked +
// Storage discharge + Conversion in above zero; Storage charge + Conversion
// out + Load stack below zero. When `season` is a specific season instead of
// 'all', renders ONE wider chart (zoom into that season) instead of the grid.
//
// Generation is rendered as one stacked <Area> per gen carrier (gas, solar,
// wind, …) using carrier-resolved colours — matching the horizon-view
// DispatchStack so the seasonal grid reads as "the same chart, averaged".
function CarrierSeasonalSection({ carrier, mode, data, season }: {
  carrier: string
  mode: 'weekly' | 'monthly'
  data: {
    seasons: Record<Season, Array<Record<string, number>>>
    genCarriers: string[]
  }
  season: Season | 'all'
}) {
  const { seasons, genCarriers } = data
  const tickFmt = (b: number) =>
    mode === 'weekly'
      ? (WEEKDAYS[Math.floor(b / 24)] ?? '')
      : `D${Math.floor(b / 24) + 1}`
  const labelFmt = (b: number) => {
    const hh = String(b % 24).padStart(2, '0')
    return mode === 'weekly'
      ? `${WEEKDAYS[Math.floor(b / 24)] ?? '?'} ${hh}:00`
      : `Day ${Math.floor(b / 24) + 1} · ${hh}:00`
  }
  // Non-generation stack keys (Storage / Stores / Conversion split into ▲/▼).
  // `Generation` is intentionally absent here — generation is rendered via
  // genCarrier-specific Areas. Energy-charts order (bottom→top): renewables,
  // storage/conversion ▲, thermal. Negative ▼ series stay below the axis
  // (stackOffset="sign").
  const POS_NON_GEN_KEYS = [
    'Storage ▲', 'Stores ▲', 'Conversion in ▲', 'Conversion out ▲',
  ]
  const NEG_NON_GEN_KEYS = [
    'Storage ▼', 'Stores ▼', 'Conversion in ▼', 'Conversion out ▼',
  ]

  const renderSeason = (sn: Season, full: boolean) => {
    const baseRows = seasons[sn] ?? []
    // Augment each row with a `LoadTotal` field = total consumption on this
    // carrier (native load + storage charge magnitude + conversion-out
    // magnitude + sink-gen absorption magnitude). The dashed line uses
    // LoadTotal so networks that encode demand via Links (electrolyzer /
    // heat-pump / datacenter) or sink generators (heat-dump) still show
    // a meaningful demand curve — matching what the horizon DispatchStack
    // does via its `load_total` series.
    const rows: Array<Record<string, number>> = baseRows.map(r => {
      const native = (r['Load'] ?? 0) as number
      const storageChg = Math.abs((r['Storage ▼'] ?? 0) as number)
      const storesChg  = Math.abs((r['Stores ▼']  ?? 0) as number)
      const convOut    = Math.abs((r['Conversion out ▼'] ?? 0) as number)
      // Sink-gen absorption: per-row sum of negative gen-carrier values.
      let sink = 0
      for (const gc of genCarriers) {
        const v = (r[`Gen:${gc}`] ?? 0) as number
        if (v < 0) sink += -v
      }
      return { ...r, LoadTotal: native + storageChg + storesChg + convOut + sink }
    })
    const presentPosNonGen = POS_NON_GEN_KEYS.filter(
      k => rows.some(r => r[k] != null && r[k] !== 0),
    )
    const presentNegNonGen = NEG_NON_GEN_KEYS.filter(
      k => rows.some(r => r[k] != null && r[k] !== 0),
    )
    const presentGenCarriers = genCarriers.filter(
      gc => rows.some(r => {
        const v = r[`Gen:${gc}`]
        return v != null && v !== 0
      }),
    )
    // Energy-charts: renewables at the base, thermal on top.
    const renewGens = presentGenCarriers.filter(gc => isRenewableCarrier(gc)).sort()
    const thermalGens = presentGenCarriers.filter(gc => !isRenewableCarrier(gc)).sort()
    const hasLoadTotal = rows.some(r => (r.LoadTotal ?? 0) !== 0)
    const hasAnything =
      presentPosNonGen.length > 0 || presentNegNonGen.length > 0
      || presentGenCarriers.length > 0 || hasLoadTotal
    const renderGenArea = (gc: string, idx: number) => (
      <Area key={`Gen:${gc}`} type="monotone" dataKey={`Gen:${gc}`}
        name={`Gen:${gc}`} stackId="dispatch"
        stroke={colourForCarrier(gc, idx)}
        fill={colourForCarrier(gc, idx)}
        fillOpacity={0.78}
        strokeWidth={0} connectNulls isAnimationActive={false} />
    )
    return (
      <div key={sn}
        className="rounded-[10px] border border-border bg-bg overflow-hidden shadow-[0_1px_0_rgba(10,14,20,0.04)]">
        <header className="flex items-center gap-2 px-4 py-2.5 border-b border-border bg-bg-2">
          <span className="w-2 h-2 rounded-full shrink-0"
            style={{ background: SEASON_ACCENT[sn] }} />
          <span className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">{sn}</span>
          <span className="text-[10px] text-muted">
            averaged {mode === 'weekly' ? 'week' : 'month'} · MW
          </span>
        </header>
        <div className="px-2 py-2">
          {rows.length === 0 || !hasAnything ? (
            <div className="py-8 text-center text-[11.5px] text-muted italic">
              No dispatch data at {carrierDisplayLabel(carrier)} buses in {sn.toLowerCase()}.
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={full ? 320 : 190}>
              <ComposedChart data={rows} stackOffset="sign"
                margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid {...CHART_GRID} />
                <XAxis dataKey="bucket" {...CHART_AXIS}
                  interval={23} tickFormatter={tickFmt} />
                <YAxis {...CHART_AXIS} label={yAxisLabel('MW')} />
                <RTooltip {...CHART_TOOLTIP}
                  labelFormatter={labelFmt}
                  formatter={(v: number, n: string) => {
                    // Strip the `Gen:` prefix so tooltips show plain carrier
                    // names (e.g. "gas", "solar") instead of "Gen:gas".
                    const label = typeof n === 'string' && n.startsWith('Gen:') ? n.slice(4) : n
                    return [`${v.toFixed(1)} MW`, label]
                  }} />
                <Legend {...CHART_LEGEND}
                  formatter={(v: string) =>
                    typeof v === 'string' && v.startsWith('Gen:') ? v.slice(4) : v
                  } />
                {/* Bottom → top: renewables, storage/conversion ▲, thermal.
                    Charge/conversion ▼ below zero. monotone curves for readability. */}
                {renewGens.map((gc, idx) => renderGenArea(gc, idx))}
                {presentPosNonGen.map(k => {
                  const base = carrierBaseGroup(k)
                  return (
                    <Area key={k} type="monotone" dataKey={k}
                      name={carrierSeriesLabel(k)} stackId="dispatch"
                      stroke={CARRIER_GROUP_STYLE[base].color}
                      fill={CARRIER_GROUP_STYLE[base].color}
                      fillOpacity={0.5}
                      strokeWidth={0} connectNulls isAnimationActive={false} />
                  )
                })}
                {thermalGens.map((gc, idx) => renderGenArea(gc, renewGens.length + idx))}
                {presentNegNonGen.map(k => {
                  const base = carrierBaseGroup(k)
                  return (
                    <Area key={k} type="monotone" dataKey={k}
                      name={carrierSeriesLabel(k)} stackId="dispatch"
                      stroke={CARRIER_GROUP_STYLE[base].color}
                      fill={CARRIER_GROUP_STYLE[base].color}
                      fillOpacity={0.35}
                      strokeWidth={0} connectNulls isAnimationActive={false} />
                  )
                })}
                {hasLoadTotal && (
                  <Line type="monotone" dataKey="LoadTotal" name="Total demand"
                    stroke={CARRIER_GROUP_STYLE.Load.color} strokeWidth={1.6}
                    strokeDasharray="4 3" dot={false} connectNulls
                    isAnimationActive={false} />
                )}
              </ComposedChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>
    )
  }
  return (
    <section>
      <header className="flex items-center gap-2 mb-2">
        <span className="text-[12px] font-semibold text-text">{carrierDisplayLabel(carrier)} — averaged {mode === 'weekly' ? 'week' : 'month'}</span>
        <span className="text-[10px] text-muted">
          renewables → storage → thermal (above zero); charge below; load dashed
        </span>
      </header>
      {season === 'all' ? (
        <div className="grid grid-cols-2 gap-4">
          {SEASONS.map(s => renderSeason(s, false))}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4">
          {renderSeason(season, true)}
        </div>
      )}
    </section>
  )
}

// ── Per-carrier KPI panel ─────────────────────────────────────────────────
// Renders the dispatch + economic KPI strips for a single bus carrier
// (electricity / H2 / heat / …). Pulls dispatch totals from the existing
// TS payloads (filtered to the carrier's gens/loads/storage), economic
// totals from the new /api/results/economics_by_carrier endpoint, and
// lost-load per carrier from /api/results/lost_load.
//
// Sits ABOVE each per-carrier DispatchStack so users see "what happened
// on this carrier" (demand met, gen split, storage, curtailment, lost-load,
// OPEX breakdown) at a glance before drilling into the hourly chart.
function CarrierKpiPanel({
  carrier, group,
  gensTS, loadTS, storPowerTS, storeTS, curtailTS, linkTS,
  generators, loads, storageUnits, stores, links,
  weightCtx, range,
  lostLoadByCarrier, econByCarrier,
  scaled, avgScale, isElectrical,
  selectedPeriod,
}: {
  carrier: string
  group: { gens: Set<string>; loads: Set<string>; storage: Set<string>; stores: Set<string>; busNames: Set<string> }
  gensTS: TSPayload | null
  loadTS: TSPayload | null
  storPowerTS: TSPayload | null
  storeTS: TSPayload | null
  curtailTS: TSPayload | null
  linkTS: TSPayload | null
  generators: Generator[]
  loads: Load[]
  storageUnits: StorageUnit[]
  stores: Store[]
  links: LinkT[]
  weightCtx: WeightCtx
  range: { from: number; to: number }
  // Pre-computed per-carrier lost-load from the parent's `lostLoadByCarrier`
  // memo — already weighted by snapshot × ipw.years and keyed by canonical
  // load carrier (electrical / hydrogen / heat / …). Single source of truth
  // avoids the panel re-computing with different (unweighted) semantics.
  lostLoadByCarrier: Array<{ carrier: string; mwh: number; cost: number }> | null
  econByCarrier: Record<string, {
    revenue_meur: { total: number; by_period: Record<string, number> }
    opex_meur:    { total: number; by_period: Record<string, number> }
    gen_cost_meur?: { total: number; by_period: Record<string, number> }
    storage_charge_cost_meur?: { total: number; by_period: Record<string, number> }
    curtailment_cost_meur?:    { total: number; by_period: Record<string, number> }
    lost_load_cost_meur?:      { total: number; by_period: Record<string, number> }
    capex_meur:   { total: number; by_period: Record<string, number> }
    dispatch_gwh: { total: number; by_period: Record<string, number> }
    lcoe_eur_per_mwh: { total: number; by_period: Record<string, number> }
  }> | null
  scaled: boolean
  avgScale: number
  isElectrical: boolean
  // Period scope label — drives the "all periods" vs "period 2030" header
  // hint AND the economics by_period slice lookup. When non-null, economics
  // come from `e.*.by_period[selectedPeriod]` instead of `e.*.total`; when
  // null, totals are used. Dispatch KPIs use `range` regardless, which
  // already collapses to the selected period via resolveRange().
  selectedPeriod?: number | string | null
}) {
  // Dispatch / demand KPIs — filtered to this carrier's gens / loads / storage.
  //
  // CONSUMPTION KPI (`demand`) on this carrier covers EVERY way energy can
  // leave the carrier's buses:
  //
  //   demand = Σ native Load.p_set (group.loads)
  //          + Σ link outflows (links with bus0 on this carrier OR with a port
  //              whose net coefficient is negative — link draining energy)
  //          + Σ sink-gen absorption (generators dispatched at p < 0 — heat-
  //              dumps, spill, etc.)
  //
  // Networks that model demand via Link consumption (datacenter, electrolyzer,
  // heat-pump consuming AC) or via heat-dump generators (heat side) would
  // otherwise show "0 demand" because `loadTS` is empty. Earlier the panel
  // only summed native loads → mismatch with the DispatchStack which already
  // shows total consumption via the dashed "load_total" line.
  //
  // GENERATION FROM LINKS (`linkGen`) is the equivalent on the supply side:
  // sum of link contributions where the link injects energy into this
  // carrier's buses (bus1 / bus2 with effective coefficient > 0). Without
  // this the H2 panel showed "0 renewable + 0 thermal" even though the
  // electrolyzer was producing hundreds of MW of H2; the heat panel hid the
  // heat pump's heat output entirely.
  //
  // LINK GEN COST (`linkGenCost`) is the marginal_cost-side analogue, used
  // to top up the panel's gen_cost KPI when the backend econByCarrier doesn't
  // already cover this (e.g., when the link's component carrier doesn't
  // match the panel's bus carrier — heat-pump-waste sits in the 'heat'
  // panel but the link's carrier is "heat-pump-waste", not "heat", so
  // backend econByCarrier['heat'] is empty).
  const dispatch = useMemo(() => {
    // Split gens into renewable vs conventional within this carrier.
    // Conventional sums NON-NEGATIVE p only (sinks have p≤0 — they belong
    // in the absorption bucket, not generation).
    const renewSet = new Set<string>()
    const thermalSet = new Set<string>()
    for (const g of generators) {
      if (!group.gens.has(g.name)) continue
      if (isRenewableCarrier(g.carrier ?? '')) renewSet.add(g.name)
      else thermalSet.add(g.name)
    }
    const renewSplit  = weightedSumSplit(gensTS, renewSet,   weightCtx, range, 'generators')
    const thermSplit  = weightedSumSplit(gensTS, thermalSet, weightCtx, range, 'generators')
    const renewable    = renewSplit.positive ?? 0
    const conventional = thermSplit.positive ?? 0
    // Sink absorption: negative-half of the gen series — heat-dumps, spill,
    // VOLL slack, etc. Added to demand because absorption is consumption
    // from this carrier's perspective.
    const sinkAbsorption = (renewSplit.negative ?? 0) + (thermSplit.negative ?? 0)
    const nativeLoad = weightedSum(loadTS, group.loads, weightCtx, range, 'generators') ?? 0
    // Combine StorageUnit (group.storage) AND Store (group.stores) dispatch.
    // PyPSA-Eur typically models H2 / heat reservoirs as Store, so without
    // adding Stores the panel reports 0 storage activity even when the LP
    // cycles a hydrogen store at hundreds of MWh/hr.
    const storageUnitSplit = weightedSumSplit(storPowerTS, group.storage, weightCtx, range, 'generators')
    const storeSplit = weightedSumSplit(storeTS, group.stores, weightCtx, range, 'stores')
    const storageSplit = {
      positive: (storageUnitSplit.positive ?? 0) + (storeSplit.positive ?? 0),
      negative: (storageUnitSplit.negative ?? 0) + (storeSplit.negative ?? 0),
    }
    // Curtailment: positive-half only (`weightedSumSplit` returns the
    // unsigned positive sum which is what we want for the % indicator).
    const curtSplit = weightedSumSplit(curtailTS, renewSet, weightCtx, range, 'generators')
    const curtailment = curtSplit.positive ?? 0

    // ── Cross-carrier link contributions to this bus carrier ─────────────
    // PER-PORT decomposition. For each link l, walk ports bus0/bus1/bus2
    // INDEPENDENTLY when each port sits on this carrier's buses:
    //   port_contribution(t) = port_coeff × p0(t)
    //   port_coeff = -1 at bus0, efficiency at bus1, efficiency2 at bus2
    // Then classify per port per snapshot:
    //   contribution > 0  → linkGen (supply this carrier)
    //   contribution < 0  → linkOutflow (consume from this carrier)
    //
    // Why per-port (not net): on a multi-port link like the heat pump
    // (bus1=highT_heat eff=2, bus2=lowT_heat eff2=-1), the NET coefficient
    // on 'heat' is +1 — which masks the bus2 consumption from low-T heat.
    // Per-port: bus1 contributes +2 × p0 (production at highT) AND bus2
    // contributes -1 × p0 (consumption at lowT). Without this split, heat
    // demand showed 0 even though the heat pump was pulling 33 MW avg of
    // low-T heat — the same number the dispatch chart's "Conversion out ▼"
    // band visualises.
    //
    // We do NOT compute link marginal cost here — the backend's
    // `economics_by_carrier` already folds each link's marginal_cost × |p0|
    // into the gen_cost / opex of the link's COMPONENT carrier (verified: the
    // H2 carrier's backend gen_cost = electrolyzer mc × |p0| exactly). The
    // panel's economics section aggregates those backend numbers across every
    // component carrier whose assets output onto this bus carrier, so adding
    // a frontend link-cost term on top would double-count.
    let linkGen = 0, linkOutflow = 0
    if (linkTS && linkTS.columns.length > 0 && group.busNames.size > 0) {
      type Port = { coeff: number }
      const meta = new Map<string, Port[]>()
      for (const l of links) {
        const ports: Port[] = []
        if (l.bus0 && group.busNames.has(l.bus0)) ports.push({ coeff: -1 })
        if (l.bus1 && group.busNames.has(l.bus1)) ports.push({ coeff: l.efficiency ?? 1 })
        const bus2 = (l as { bus2?: string; efficiency2?: number }).bus2
        const eff2 = (l as { bus2?: string; efficiency2?: number }).efficiency2
        if (bus2 && group.busNames.has(bus2)) ports.push({ coeff: eff2 ?? 1 })
        if (ports.length === 0) continue
        meta.set(l.name, ports)
      }
      const lastRow = linkTS.data.length - 1
      const rFrom = Math.max(0, Math.min(range.from, lastRow))
      const rTo   = Math.max(0, Math.min(range.to,   lastRow))
      if (rFrom <= rTo) {
        const sw = weightCtx?.snapshotWeights ?? []
        const periods = weightCtx?.snapshotPeriods ?? linkTS.periods ?? []
        const pwArr = weightCtx?.periodWeights
        for (let row = rFrom; row <= rTo; row++) {
          let w = 1
          if (sw[row]) {
            const v = sw[row].generators
            if (typeof v === 'number' && Number.isFinite(v)) w *= v
          }
          const periodVal = periods[row]
          if (periodVal != null && pwArr) {
            const pr = pwArr.find(r => r.period === periodVal)
            if (pr) {
              const y = pr.years
              if (typeof y === 'number' && Number.isFinite(y)) w *= y
            }
          }
          for (let i = 0; i < linkTS.columns.length; i++) {
            const ports = meta.get(linkTS.columns[i])
            if (!ports) continue
            const p0 = linkTS.data[row][i]
            if (!Number.isFinite(p0)) continue
            for (const port of ports) {
              const contrib = port.coeff * p0
              if (contrib > 0) linkGen     += contrib * w
              else if (contrib < 0) linkOutflow += -contrib * w
            }
          }
        }
      }
    }

    const demand = nativeLoad + Math.abs(sinkAbsorption) + linkOutflow

    // Curtailment % of total renewable potential (delivered + curtailed) —
    // matches the renewable-curtailment KPI in the legacy aggregated panel.
    const renewablePotential = renewable + curtailment
    const curtailmentPct = renewablePotential > 0
      ? (curtailment / renewablePotential) * 100
      : null

    // Total generation = native + link gen (carrier-side production picture).
    const totalGen = renewable + conventional + linkGen
    return {
      conventional,
      renewable,
      demand,
      nativeLoad,
      sinkAbsorption: Math.abs(sinkAbsorption),
      linkGen,
      linkOutflow,
      totalGen,
      storageDischarge: storageSplit.positive,
      storageCharge:    storageSplit.negative,
      curtailment,
      curtailmentPct,
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gensTS, loadTS, storPowerTS, storeTS, curtailTS, linkTS, generators, links, group, weightCtx, range.from, range.to])

  // Lost-load energy + cost for this BUS carrier — read from the parent's
  // weighted per-carrier rollup. Keys there are canonical
  // (electrical / hydrogen / heat / …); map the panel's raw bus carrier
  // via `loadCarrierKey`.
  const lostLoadRow = useMemo(() => {
    if (!lostLoadByCarrier) return null
    const key = loadCarrierKey(carrier)
    return lostLoadByCarrier.find(r => r.carrier === key) ?? null
  }, [lostLoadByCarrier, carrier])
  const lostLoadEnergy = lostLoadRow?.mwh ?? 0
  const lostLoadCostFromCarrier = lostLoadRow?.cost ?? 0

  // Economic KPIs — aggregate `econByCarrier` (keyed by COMPONENT carrier,
  // e.g., "gas", "solar", "heat-pump-waste") across all component carriers
  // whose assets sit on this BUS carrier. Without this aggregation, an
  // electricity bus panel (bus carrier "AC") would show empty economics
  // because backend has no carrier called "ac" — it has "gas", "solar",
  // "battery" each keyed under its own component carrier.
  // Rules:
  //   • Generator → component carrier counts if generator.bus ∈ busNames
  //   • StorageUnit → ditto
  //   • Link → counts if its OUTPUT side (bus1 or bus2) ∈ busNames. The
  //     link's carrier (electrolyzer="H2", heat-pump-waste, datacenter)
  //     identifies what it produces, so its economics belong to the
  //     producing-carrier panel.
  const econ = useMemo(() => {
    if (!econByCarrier) return null
    const componentCarriers = new Set<string>()
    for (const g of generators) {
      if (group.gens.has(g.name)) {
        componentCarriers.add((g.carrier ?? '').toLowerCase())
      }
    }
    for (const s of storageUnits) {
      if (group.storage.has(s.name)) {
        componentCarriers.add((s.carrier ?? '').toLowerCase())
      }
    }
    for (const l of links) {
      const bus2 = (l as { bus2?: string }).bus2
      const linkOutputsHere =
        (l.bus1 && group.busNames.has(l.bus1)) ||
        (!!bus2 && group.busNames.has(bus2))
      if (linkOutputsHere) {
        componentCarriers.add((l.carrier ?? '').toLowerCase())
      }
    }
    if (componentCarriers.size === 0) return null
    // Period-scoped vs horizon-total lookups. When `selectedPeriod` is set,
    // pull from `e.*.by_period[period]` so the panel shows just that period's
    // economics (matches the KPI strip's range scoping). When aggregated,
    // sum the `total` field — equivalent to the horizon-wide sum.
    const pickValue = (
      bucket: { total: number; by_period: Record<string, number> } | undefined,
    ): number => {
      if (!bucket) return 0
      if (selectedPeriod != null) {
        const k = String(selectedPeriod)
        return bucket.by_period?.[k] ?? 0
      }
      return bucket.total ?? 0
    }
    let revenue = 0, opex = 0, gen_cost = 0, charge = 0, curt = 0, lost = 0, capex = 0, dispatch = 0
    let hit = false
    for (const cc of componentCarriers) {
      const e = econByCarrier[cc]
      if (!e) continue
      hit = true
      revenue  += pickValue(e.revenue_meur)
      opex     += pickValue(e.opex_meur)
      gen_cost += pickValue(e.gen_cost_meur)
      charge   += pickValue(e.storage_charge_cost_meur)
      curt     += pickValue(e.curtailment_cost_meur)
      lost     += pickValue(e.lost_load_cost_meur)
      capex    += pickValue(e.capex_meur)
      dispatch += pickValue(e.dispatch_gwh)
    }
    if (!hit) return null
    const lcoe = dispatch > 1e-9 ? ((capex + opex) / dispatch) * 1000 : 0
    return { revenue, opex, gen_cost, charge, curt, lost, capex, dispatch, lcoe }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [econByCarrier, generators, storageUnits, links, group, selectedPeriod])

  const scaleHint = scaled ? ` × ${avgScale.toFixed(2)} weighted` : ''
  // Scope label: tells the user whether the numbers cover the full horizon,
  // a single period, or a user-narrowed slice. Reads off the same range +
  // selectedPeriod the KPI maths uses, so the label always matches the data.
  const scopeLabel = selectedPeriod != null
    ? `period ${selectedPeriod}`
    : 'all periods (horizon total)'

  return (
    <section className="rounded-[10px] border border-border bg-bg-2 p-3.5">
      <header className="flex items-center justify-between mb-2.5">
        <h3 className="text-[13px] font-semibold text-text tracking-[-0.005em] flex items-center gap-2">
          {carrierDisplayLabel(carrier)}
          <span className="text-[10px] font-normal text-muted">
            {isElectrical ? 'electrical bus balance' : `${carrierDisplayLabel(carrier)} bus balance`}
          </span>
          <span className="text-[10px] font-normal text-accent bg-accent/10 px-1.5 py-0.5 rounded">
            {scopeLabel}
          </span>
        </h3>
        {scaled && (
          <span className="text-[10px] font-normal text-accent bg-accent/10 px-1.5 py-0.5 rounded font-mono">
            ×{avgScale.toFixed(2)} weighted
          </span>
        )}
      </header>

      {/* Dispatch + demand KPIs */}
      <h4 className="text-[10.5px] font-semibold text-muted uppercase tracking-wide mb-1.5">
        Dispatch &amp; demand
      </h4>
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-7 gap-2 mb-3">
        <KPI label="Total demand" value={fmtEnergy(dispatch.demand)}
             hint={`Total energy consumed on ${carrierDisplayLabel(carrier)} buses: native loads (${fmtEnergy(dispatch.nativeLoad)}) + link outflows (${fmtEnergy(dispatch.linkOutflow)}, e.g. electrolyzer / heat-pump / datacenter draining this carrier) + sink-gen absorption (${fmtEnergy(dispatch.sinkAbsorption)}, e.g. heat-dump). Storage charging is shown separately below.${scaleHint}`} />
        <KPI label="Gen — conventional"  value={fmtEnergy(dispatch.conventional)}  hint={`Σ max(p,0) × weights over thermal gens on this carrier${scaleHint}`} />
        <KPI label="Gen — renewable"     value={fmtEnergy(dispatch.renewable)}     hint={`Σ max(p,0) × weights over renewable gens on this carrier${scaleHint}`} />
        <KPI label="Gen — links" value={dispatch.linkGen > 1e-6 ? fmtEnergy(dispatch.linkGen) : '—'}
             hint={`Σ link contributions where the link's bus1/bus2 sits on ${carrierDisplayLabel(carrier)} buses (efficiency × p0 × weights). Captures electrolyzer H₂ output, heat-pump heat output, datacenter waste heat, etc.${scaleHint}`} />
        <KPI label="Storage discharge"
             value={fmtEnergy(dispatch.storageDischarge ?? 0)}
             hint={group.storage.size + group.stores.size === 0
               ? `No StorageUnit or Store on ${carrierDisplayLabel(carrier)} buses — add one to model reservoir cycling on this carrier.`
               : `Σ max(p,0) × weights over ${group.storage.size} StorageUnit(s) + ${group.stores.size} Store(s) on this carrier.`} />
        <KPI label="Storage charge"
             value={fmtEnergy(dispatch.storageCharge ?? 0)}
             hint={group.storage.size + group.stores.size === 0
               ? `No StorageUnit or Store on ${carrierDisplayLabel(carrier)} buses.`
               : `Σ |min(p,0)| × weights over ${group.storage.size} StorageUnit(s) + ${group.stores.size} Store(s) on this carrier.`} />
        <KPI label="Curtailment"
             value={dispatch.curtailment != null && dispatch.curtailment > 1e-6
               ? `${fmtEnergy(dispatch.curtailment)}${dispatch.curtailmentPct != null ? ` · ${dispatch.curtailmentPct.toFixed(1)} %` : ''}`
               : '—'}
             hint={`Σ curtailed energy (server pre-computes max(p_max_pu × p_nom − p, 0)) × weights, over renewables on ${carrierDisplayLabel(carrier)} buses. % = curtailment ÷ (curtailment + renewable_dispatch).${scaleHint}`} />
        <KPI label="Lost load"           value={lostLoadEnergy > 1e-6 ? fmtEnergy(lostLoadEnergy) : '—'}
             hint={`VOLL slack dispatched on ${carrierDisplayLabel(carrier)} buses — non-zero only when the LP couldn't meet demand`} />
      </div>

      {/* Economic KPIs — aggregated over all component carriers whose
          assets sit on this bus carrier (gas + solar + battery → 'AC' panel;
          heat-dump + heat-pump-waste + datacenter + heat-storage → 'heat'). */}
      <h4 className="text-[10.5px] font-semibold text-muted uppercase tracking-wide mb-1.5">
        Economics
      </h4>
      {econ === null ? (
        <div className="text-[10.5px] text-muted italic">
          No economic data for this carrier — assets may be brownfield-only with no dispatch, or the backend response is missing.
        </div>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-7 gap-2">
          {/* Economics come straight from the backend `economics_by_carrier`
              (aggregated across every component carrier whose assets output
              onto this bus carrier). The backend already folds each link's
              marginal_cost × |p0| into the link component-carrier's gen_cost /
              opex, so there is NO frontend top-up — adding one double-counts
              (verified: H2 backend gen_cost = electrolyzer mc × |p0| exactly).
              LCOE uses the frontend-computed carrier dispatch (native gen +
              link gen + storage discharge) so the denominator matches the
              energy KPIs shown above. */}
          {(() => {
            const genCostEur = econ.gen_cost * 1e6
            const opexEur = econ.opex * 1e6
            const dispatchTotal = dispatch.totalGen + (dispatch.storageDischarge ?? 0)
            const lcoe = dispatchTotal > 1e-6 ? (opexEur + econ.capex * 1e6) / dispatchTotal : 0
            return (
              <>
                <KPI accent label="OPEX (total)" value={fmtCurrency(opexEur)}
                     hint="Broader than Capacity Expansion's OPEX (horizon): Σ gen/link marginal cost + storage charge at bus price + curtailment + lost-load (economics_by_carrier). For this run, gen cost alone matches Capacity Expansion; the extra is mainly Storage charge cost." />
                <KPI label="Gen cost" value={fmtCurrency(genCostEur)}
                     hint={`Σ marginal_cost × |dispatch| × weights over gens / links producing on this carrier — this is what Capacity Expansion labels OPEX (horizon). Link costs (e.g. electrolyzer / P2H marginal_cost × |p0|) are already included by the backend.${scaleHint}`} />
                <KPI label="Storage charge cost" value={Math.abs(econ.charge) > 1e-6 ? fmtCurrency(econ.charge * 1e6) : '—'}
                     hint="What storage paid (or earned, if negative) to charge — bus_price × |charge| × weights. Included in OPEX (total) here, but NOT in Capacity Expansion OPEX (horizon)." />
                <KPI label="Curtailment cost" value={econ.curt > 1e-6 ? fmtCurrency(econ.curt * 1e6) : '—'}
                     hint="Σ curtailment_cost × curtailed-energy × weights" />
                <KPI label="Lost-load cost" value={(econ.lost > 1e-6 || lostLoadCostFromCarrier > 1e-3)
                                                    ? fmtCurrency(Math.max(econ.lost * 1e6, lostLoadCostFromCarrier)) : '—'}
                     hint="VOLL × unserved demand on this carrier's buses" />
                <KPI label="CAPEX (annuitised)" value={fmtCurrency(econ.capex * 1e6)}
                     hint="Σ (overnight × annuity, or capital_cost) × p_nom_opt × years_in_horizon — same gens/storage basis as Economics Fixed cost (excludes line CAPEX)." />
                <KPI label="LCOE" value={lcoe > 1e-6 ? `${lcoe.toFixed(1)} €/MWh` : '—'}
                     hint="(CAPEX + OPEX) ÷ (gen + link gen + storage discharge) — aggregated across this carrier's component-carriers" />
              </>
            )
          })()}
        </div>
      )}
    </section>
  )
}

// ── Collapsible per-group section ─────────────────────────────────────────────
// Mode toggle: aggregated (default) ↔ per-asset. Per-asset chart picks the
// top-N traces by mean absolute value so a 1000-asset network doesn't render
// 1000 lines.
// Vintage results payload — keyed by component class then asset name. The
// `periods` array carries one entry per vintage build_year with its solved
// p_nom_opt. Used to compute period-active energy capacity for SoC % view.
interface VintageResultsPayload {
  results: Record<string, Record<string, {
    capacity_field: string
    initial_capacity: number
    periods: Array<{ build_year: number; p_nom_opt: number; p_nom_min: number; p_nom_max: number | null }>
  }>>
}

interface GroupSectionProps<T> {
  groupKey: GroupKey
  assets: T[]
  nameField: keyof T & string
  carrierField: keyof T & string
  agg: ReturnType<typeof aggregateTS>
  ts: TSPayload | null
  memberSet: Set<string>
  unitOverride?: string
  emptyHint?: string
  // Optional aggregated curtailment time-series for this group. Currently
  // only Renewables passes it; when present, a "Curtailment" toggle on the
  // section header reveals a sibling chart of curtailed MW vs time.
  curtailmentSeries?: ReturnType<typeof aggregateTS>
  // Optional aggregated lost-load time-series. Mirrors the curtailment
  // pattern: passed only by the Load section, surfaces a "Lost load" toggle
  // that reveals a sibling chart of unmet-demand MW vs time. Aggregate
  // totals (MWh + €) come from `lostLoadTotals` so a single header line
  // shows Σ over the horizon at a glance.
  lostLoadSeries?: ReturnType<typeof aggregateTS>
  lostLoadTotals?: { mwh: number; cost: number } | null
  // Vintage results + per-snapshot period array — only needed for the
  // energy-reservoir SoC % view in multi-period runs, so the denominator
  // shrinks to the period-active capacity instead of the cumulative final
  // value. Without these, the % bar in early periods reads ~25% of full
  // because the denom counts vintages that don't exist yet.
  vintageResults?: VintageResultsPayload
  snapshotPeriods?: Array<number | string>
  // Override the header label. Used by the Links section to render one
  // section per link carrier (e.g. "Links · DC", "Links · Electrolyser")
  // while keeping the shared Links visual style (icon, blue accent).
  titleOverride?: string
  // Horizon-filter row range — applies to BOTH aggregated (`agg`, already
  // sliced upstream by aggregateTS) AND the per-asset view (which derives
  // straight from `ts` and so needs its own slice). Without this, switching
  // to "Per asset" while a horizon filter is active spuriously shows the
  // full year while the aggregated view only shows the filtered week.
  rowRange?: { from: number; to: number }
}
// ── CarrierTabbedSection ─────────────────────────────────────────────────────
// Wraps multiple per-carrier GroupSections under a single header with a
// carrier picker chip strip. Only the active carrier's chart is rendered,
// keeping the page short while preserving the per-carrier separation the
// data demands (mixing H2 + heat + electrical on the same axis is mixed-
// units nonsense).
//
// State (`active`) is local — collapsing/uncollapsing a single tab is what
// the user expects here, not page-global state. The active carrier
// defaults to the first one in the list (sort order is set by the caller
// via `loadCarrierSortKey` so "Electrical" comes first).
//
// `extraPropsForCarrier` is an escape hatch for per-carrier behaviour like
// "only show lost-load overlay on the electrical tab" — the caller returns
// per-tab GroupSection props to merge in.

interface CarrierTabbedSectionProps<T extends { name: string }> {
  carriers: Array<{
    id: string
    label: string
    assets: T[]
    agg: ReturnType<typeof aggregateTS>
    nameSet: Set<string>
  }>
  groupKey: GroupKey
  ts: TSPayload | null
  rowRange?: { from: number; to: number }
  unitOverride?: string
  extraPropsForCarrier?: (carrierId: string) => Partial<{
    lostLoadSeries: ReturnType<typeof aggregateTS>
    lostLoadTotals: { mwh: number; cost: number } | null
    curtailmentSeries: ReturnType<typeof aggregateTS>
  }>
}

function CarrierTabbedSection<T extends { name: string; carrier?: string }>(props: CarrierTabbedSectionProps<T>) {
  const { carriers, groupKey, ts, rowRange, unitOverride, extraPropsForCarrier } = props
  const [active, setActive] = useState<string>(() => carriers[0]?.id ?? '')
  // Re-pin the active tab if the carrier list shrinks under us (e.g. a load
  // got removed). Without this the section renders blank because `current`
  // looks up a carrier that no longer exists.
  if (carriers.length > 0 && !carriers.some(c => c.id === active)) {
    // Defer the state update until after this render — calling setActive
    // synchronously here would re-enter useState's update path. The render
    // path below falls back to carriers[0] when no match is found.
    queueMicrotask(() => setActive(carriers[0].id))
  }
  const current = carriers.find(c => c.id === active) ?? carriers[0]
  if (!current) return null
  const extra = extraPropsForCarrier?.(current.id) ?? {}
  const style = GROUP_STYLE[groupKey]
  const Icon = style.Icon

  return (
    <section className="rounded-[10px] border border-border bg-bg overflow-hidden shadow-[0_1px_0_rgba(10,14,20,0.04)]">
      {/* Tab strip — slim chip row above the actual chart. Chips inherit the
          same colour-coded icon as the group so the visual identity carries
          across the per-carrier views. */}
      <header className="flex items-center gap-2 px-4 py-2 border-b border-border bg-bg-2">
        <Icon size={14} color={style.color} />
        <span className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">{groupKey}</span>
        <span className="text-[10px] text-muted">{carriers.length} carrier{carriers.length === 1 ? '' : 's'}</span>
        <div className="ml-auto flex items-center gap-1">
          {carriers.map(c => (
            <button
              key={c.id}
              onClick={() => setActive(c.id)}
              className={
                'text-[10px] px-2 py-0.5 rounded transition-colors ' +
                (c.id === current.id
                  ? 'bg-accent text-white'
                  : 'bg-bg border border-border text-text hover:bg-border/30')
              }
              title={`Show only "${c.label}" loads (${c.assets.length} asset${c.assets.length === 1 ? '' : 's'})`}
            >
              {c.label}
            </button>
          ))}
        </div>
      </header>
      <div className="px-4 py-3">
        {/* Reuse the existing GroupSection for the body — we strip its own
            header by hiding the wrapping <section> styles via a nested
            render. The simplest robust path: render GroupSection inline
            and accept its own header showing the carrier-specific title. */}
        <GroupSection
          groupKey={groupKey}
          titleOverride={`${groupKey} · ${current.label}`}
          assets={current.assets}
          nameField="name"
          carrierField="carrier"
          agg={current.agg}
          ts={ts}
          memberSet={current.nameSet}
          unitOverride={unitOverride}
          rowRange={rowRange}
          {...extra}
        />
      </div>
    </section>
  )
}

function GroupSection<T extends { name: string }>(props: GroupSectionProps<T>) {
  const { groupKey, assets, agg, ts, memberSet, unitOverride, emptyHint,
          curtailmentSeries, lostLoadSeries, lostLoadTotals,
          vintageResults, snapshotPeriods, titleOverride, rowRange } = props
  const style = GROUP_STYLE[groupKey]
  const Icon = style.Icon
  const [open, setOpen] = useState(true)
  const [perAsset, setPerAsset] = useState(false)
  // % of installed energy capacity toggle. Only relevant for the two
  // energy-reservoir groups — for power dispatch the percent equivalent
  // would be % of nameplate which is already shown elsewhere as loading %.
  const isEnergyReservoir = groupKey === 'Storage SoC' || groupKey === 'Stores Energy'
  const [pctMode, setPctMode] = useState(false)
  // Final (cumulative) capacity per asset — used as the % denominator on
  // flat single-period runs and as the fallback when no vintage info exists.
  //   Storage SoC  → Σ (p_nom_opt × max_hours)   (energy ceiling at full charge)
  //   Stores Energy → Σ e_nom_opt                  (energy ceiling of the reservoir)
  // p_nom_opt / e_nom_opt fall back to p_nom / e_nom for unsolved or
  // non-extendable assets. Excluded any asset that maps to 0 capacity to
  // avoid divide-by-zero (those traces just stay at 0).
  const capacity = useMemo(() => {
    if (!isEnergyReservoir) return { total: 0, byName: new Map<string, number>() }
    const byName = new Map<string, number>()
    let total = 0
    for (const a of assets as unknown as Array<Record<string, number | undefined>>) {
      const name = (a as unknown as { name: string }).name
      let cap = 0
      if (groupKey === 'Storage SoC') {
        const nom = (a.p_nom_opt ?? a.p_nom) ?? 0
        const hrs = a.max_hours ?? 0
        cap = Number.isFinite(nom) && Number.isFinite(hrs) ? (nom as number) * (hrs as number) : 0
      } else {
        // Stores Energy: e_nom_opt is the energy ceiling.
        const en = (a.e_nom_opt ?? a.e_nom) ?? 0
        cap = Number.isFinite(en) ? (en as number) : 0
      }
      byName.set(name, cap)
      total += cap
    }
    return { total, byName }
  }, [isEnergyReservoir, groupKey, assets])
  // Per-period denominator for the SoC % view. Computed as max(|value|)
  // per (asset, period) WITHIN THE CURRENT HORIZON FILTER WINDOW, so the
  // peak SoC visible in the chart always reads as 100 %. Outside-window
  // values don't influence the scale (a 7-day filter wouldn't punish the
  // chart by anchoring 100 % to a summer-only peak the user can't see).
  //
  // Three layers of denom resolution at render time:
  //   1. PRIMARY — this window-limited period max (computed here). Gives
  //      "% of peak observed in this window/period". Peak = 100 %.
  //   2. FALLBACK — if a column has no data in window (all zero or NaN),
  //      derived denom = 0 and the consumer falls back to capacity.byName
  //      (final cumulative nameplate × max_hours).
  //
  // Why range-limited and not period-wide?
  //   The user filters to e.g. Jan 1-7 of 2026 and expects to see what
  //   FRACTION of the battery they're using THIS WEEK. Period-wide max
  //   anchors 100 % to whichever week cycles fullest, leaving every other
  //   week looking like 25 % even though the LP IS cycling fully there
  //   relative to the period's effective capacity.
  const capacityByPeriod = useMemo(() => {
    const total = new Map<number | string, number>()
    const byNameByPeriod = new Map<string, Map<number | string, number>>()
    if (!isEnergyReservoir) return { total, byNameByPeriod }
    if (!ts || !snapshotPeriods || ts.index.length !== snapshotPeriods.length) {
      return { total, byNameByPeriod }
    }
    // Slice to the same horizon range the rest of the section uses. When
    // rowRange is unset (no horizon filter), iterate the full series.
    const lastRow = ts.data.length - 1
    const rFrom = Math.max(0, Math.min(rowRange?.from ?? 0, lastRow))
    const rTo   = Math.max(0, Math.min(rowRange?.to ?? lastRow, lastRow))
    if (rFrom > rTo) return { total, byNameByPeriod }

    // Per-asset per-period max within window.
    for (let row = rFrom; row <= rTo; row++) {
      const p = snapshotPeriods[row]
      if (p == null) continue
      for (let col = 0; col < ts.columns.length; col++) {
        if (!memberSet.has(ts.columns[col])) continue
        const v = ts.data[row][col]
        if (!Number.isFinite(v)) continue
        const abs = Math.abs(v)
        if (abs <= 0) continue
        const name = ts.columns[col]
        let perAsset = byNameByPeriod.get(name)
        if (!perAsset) {
          perAsset = new Map<number | string, number>()
          byNameByPeriod.set(name, perAsset)
        }
        const prev = perAsset.get(p) ?? 0
        if (abs > prev) perAsset.set(p, abs)
      }
    }

    // Aggregated denominator: per-snapshot sum across in-group assets, then
    // max within each period (still range-limited). Matches the agg-series
    // scale so the peak in the aggregated chart also lands at 100 %.
    const aggMaxByPeriod = new Map<number | string, number>()
    for (let row = rFrom; row <= rTo; row++) {
      const p = snapshotPeriods[row]
      if (p == null) continue
      let s = 0
      for (let col = 0; col < ts.columns.length; col++) {
        if (!memberSet.has(ts.columns[col])) continue
        const v = ts.data[row][col]
        if (Number.isFinite(v)) s += v
      }
      const abs = Math.abs(s)
      const prev = aggMaxByPeriod.get(p) ?? 0
      if (abs > prev) aggMaxByPeriod.set(p, abs)
    }
    for (const [p, v] of aggMaxByPeriod) total.set(p, v)

    return { total, byNameByPeriod }
  }, [isEnergyReservoir, ts, snapshotPeriods, memberSet, rowRange?.from, rowRange?.to])
  const hasPerPeriodCapacity = capacityByPeriod.total.size > 0
  const showPct = pctMode && isEnergyReservoir && (capacity.total > 0 || hasPerPeriodCapacity)
  const yUnit = showPct ? '%' : (unitOverride ?? style.unit)
  // Optional curtailment overlay (Renewables only). Off by default — many
  // users don't care about curtailment so we don't clutter the section.
  const [showCurtailment, setShowCurtailment] = useState(false)
  const hasCurtailment = curtailmentSeries != null && curtailmentSeries.length > 0
  // Optional lost-load overlay (Load section only). Same UX pattern as
  // curtailment — toggle in the header, sibling chart below the main one.
  const [showLostLoad, setShowLostLoad] = useState(false)
  const hasLostLoad = lostLoadSeries != null && lostLoadSeries.length > 0

  // Per-asset per-snapshot table — slices to the same horizon range the
  // aggregated view uses. Without the slice, switching to "Per asset" while
  // a horizon filter is active spuriously expands back to the full series.
  // Top-N is selected on the FULL series so the same assets are picked
  // regardless of which week the user is currently viewing (otherwise the
  // visible set would shuffle every time the filter moves).
  const perAssetData = useMemo(() => {
    if (!ts || ts.data.length === 0) return null
    const colIdxs = ts.columns
      .map((c, i) => ({ c, i }))
      .filter(o => memberSet.has(o.c))
    if (colIdxs.length === 0) return null

    // Top-N selection by mean |value| — uses the FULL series so the asset
    // ranking stays stable across horizon-filter moves.
    const meanAbs = colIdxs.map(({ i }) => {
      let s = 0, n = 0
      for (const r of ts.data) {
        const v = r[i]
        if (Number.isFinite(v)) { s += Math.abs(v); n += 1 }
      }
      return n ? s / n : 0
    })
    const ordered = colIdxs
      .map((o, k) => ({ ...o, mean: meanAbs[k] }))
      .sort((a, b) => b.mean - a.mean)
      .slice(0, PER_ASSET_TOP_N)

    // Apply the same inclusive [from, to] slice as `aggregateTS` so the
    // aggregated and per-asset views always cover identical snapshots.
    const lastRow = ts.data.length - 1
    const rFrom = Math.max(0, Math.min(rowRange?.from ?? 0, lastRow))
    const rTo   = Math.max(0, Math.min(rowRange?.to ?? lastRow, lastRow))
    if (rFrom > rTo) return { rows: [], cols: ordered.map(o => o.c) }

    const rows: Array<Record<string, number | string>> = []
    for (let row = rFrom; row <= rTo; row++) {
      const obj: Record<string, number | string> = { snapshot: ts.index[row] }
      for (const { c, i } of ordered) {
        const v = ts.data[row][i]
        obj[c] = Number.isFinite(v) ? v : 0
      }
      rows.push(obj)
    }
    return { rows, cols: ordered.map(o => o.c) }
  }, [ts, memberSet, rowRange?.from, rowRange?.to])

  // % transformed views. We do this transformation here rather than back at
  // the data source so toggling is instant — no refetch needed. The denom
  // is per-asset capacity for the per-asset chart, total capacity for the
  // aggregated chart.
  //
  // Multi-period awareness: when vintage_results is available AND each
  // snapshot's period is known (parallel `snapshotPeriods` array), we use
  // the period-active capacity as the denominator. In period P that means
  // initial_capacity + Σ vintage builds with build_year ≤ P — so an SoC
  // of 100 MWh in period 2026 against an active 130 MWh capacity reads
  // 77%, not the misleading 15% it would read against the cumulative
  // final-year 660 MWh (2026 + 2027 + 2028 vintages all summed).
  //
  // Need ts to map agg row → its absolute index → period. ts.index has the
  // ISO timestamp for every row; agg rows expose ISO via `snapshot` field
  // (matches ts.index[i]) — but the agg may be slice-filtered by the
  // horizon filter, so we look up each row's period by ISO directly.
  const periodByStamp = useMemo(() => {
    if (!snapshotPeriods || !ts || ts.index.length !== snapshotPeriods.length) return null
    const m = new Map<string, number | string>()
    for (let i = 0; i < ts.index.length; i++) m.set(ts.index[i], snapshotPeriods[i])
    return m
  }, [snapshotPeriods, ts])
  const aggView = useMemo(() => {
    if (!agg) return null
    if (!showPct) return agg
    return agg.map(r => {
      // Prefer per-period denominator when we know the period for this row.
      const p = periodByStamp?.get(r.snapshot)
      const periodDenom = (p != null && capacityByPeriod.total.get(p)) || 0
      const denom = periodDenom > 0 ? periodDenom : capacity.total
      if (denom <= 0) return r
      return { ...r, value: (r.value / denom) * 100 }
    })
  }, [agg, showPct, capacity.total, capacityByPeriod.total, periodByStamp])
  const perAssetView = useMemo(() => {
    if (!perAssetData) return null
    if (!showPct) return perAssetData
    return {
      cols: perAssetData.cols,
      rows: perAssetData.rows.map(r => {
        const out: Record<string, number | string> = { snapshot: r.snapshot }
        for (const c of perAssetData.cols) {
          // Per-asset, per-period denominator. Falls back to per-asset
          // cumulative if the asset has no vintages.
          const p = periodByStamp?.get(r.snapshot as string)
          const periodCap = (p != null && capacityByPeriod.byNameByPeriod.get(c)?.get(p)) || 0
          const cap = periodCap > 0 ? periodCap : (capacity.byName.get(c) ?? 0)
          const v = r[c]
          out[c] = cap > 0 && typeof v === 'number' ? (v / cap) * 100 : 0
        }
        return out
      }),
    }
  }, [perAssetData, showPct, capacity.byName, capacityByPeriod.byNameByPeriod, periodByStamp])

  const exportCSV = () => {
    if (perAsset && perAssetData) {
      downloadCSV(`results_${groupKey.toLowerCase()}_per_asset.csv`,
        ['snapshot', ...perAssetData.cols],
        perAssetData.rows.map(r => [r.snapshot, ...perAssetData.cols.map(c => r[c])]),
      )
      toast.success('Exported per-asset CSV')
    } else if (agg) {
      // Sanitise the unit string so we don't end up with ugly "Storage (MWh
      // (state of charge))" headers when unitOverride already wraps in parens.
      const unit = (unitOverride ?? style.unit).replace(/[()]/g, '').trim()
      downloadCSV(`results_${groupKey.toLowerCase()}_aggregated.csv`,
        ['snapshot', `${groupKey} ${unit}`],
        agg.map(r => [r.snapshot, r.value.toFixed(3)]),
      )
      toast.success(`Exported ${agg.length} rows`)
    } else {
      toast.error('No data to export')
    }
  }

  // LineChart for any storage-related view: signed dispatch series have negative
  // values (charging) which AreaChart can't shade well, and SoC/Energy values
  // are best shown as a continuous line. AreaChart stays for generation/load
  // groups where the value is always non-negative.
  const isStorage = groupKey === 'Storage' || groupKey === 'Storage SoC'
                  || groupKey === 'Stores' || groupKey === 'Stores Energy'

  // Refs for SVG export. Three potential charts per section: main, curtailment
  // overlay (Renewables), lost-load overlay (Load). Each is in its own div so
  // each can be exported independently from its sibling.
  const mainChartRef    = useRef<HTMLDivElement | null>(null)
  const curtailChartRef = useRef<HTMLDivElement | null>(null)
  const lostLoadChartRef = useRef<HTMLDivElement | null>(null)
  const fileBase = `results_${groupKey.toLowerCase().replace(/\s+/g, '_')}`
  const exportCurtailmentCSV = () => {
    if (!curtailmentSeries) return
    downloadCSV(`${fileBase}_curtailment.csv`,
      ['snapshot', 'curtailed_MW'],
      curtailmentSeries.map(r => [r.snapshot, r.value.toFixed(3)]))
    toast.success('Exported')
  }
  const exportLostLoadCSV = () => {
    if (!lostLoadSeries) return
    downloadCSV(`${fileBase}_lost_load.csv`,
      ['snapshot', 'lost_load_MW'],
      lostLoadSeries.map(r => [r.snapshot, r.value.toFixed(3)]))
    toast.success('Exported')
  }

  return (
    <section className="rounded-[10px] border border-border bg-bg overflow-hidden shadow-[0_1px_0_rgba(10,14,20,0.04)]">
      <header className="flex items-center gap-2 px-4 py-2.5 border-b border-border bg-bg-2 cursor-pointer select-none"
        onClick={() => setOpen(o => !o)}>
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <Icon size={14} color={style.color} />
        <span className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">{titleOverride ?? groupKey}</span>
        <span className="text-[10px] text-muted">{assets.length} asset{assets.length !== 1 ? 's' : ''}</span>
        <span className="flex-1" />
        {agg != null && (
          <button onClick={(e) => { e.stopPropagation(); setPerAsset(p => !p) }}
            className="text-[10px] text-muted hover:text-accent">
            {perAsset ? 'Aggregated' : 'Per asset'}
          </button>
        )}
        {/* Energy reservoirs (SoC / Stores Energy) — toggle MWh vs % of
            installed capacity. Hidden when no capacity is configured (any
            divide-by-zero would be misleading). */}
        {isEnergyReservoir && capacity.total > 0 && (
          <button onClick={(e) => { e.stopPropagation(); setPctMode(p => !p) }}
            title="Toggle between absolute MWh and % of total installed energy capacity"
            className={`text-[10px] ml-1 transition-colors ${pctMode ? 'text-accent font-semibold' : 'text-muted hover:text-accent'}`}>
            {pctMode ? '%' : 'MWh'}
          </button>
        )}
        {hasCurtailment && (
          <button onClick={(e) => { e.stopPropagation(); setShowCurtailment(s => !s) }}
            title="Toggle a sibling chart of curtailed renewable power vs time"
            className={`flex items-center gap-1 text-[10px] ml-1 transition-colors
              ${showCurtailment ? 'text-amber-600 font-semibold' : 'text-muted hover:text-accent'}`}>
            <Scissors size={10} />
            Curtailment
          </button>
        )}
        {hasLostLoad && (
          <button onClick={(e) => { e.stopPropagation(); setShowLostLoad(s => !s) }}
            title="Toggle a sibling chart of unmet load (VOLL slack dispatch) vs time"
            className={`flex items-center gap-1 text-[10px] ml-1 transition-colors
              ${showLostLoad ? 'text-danger font-semibold' : 'text-muted hover:text-accent'}`}>
            <Zap size={10} />
            Lost load
          </button>
        )}
        {/* SVG + CSV exports for the MAIN chart. Stop propagation so clicking
            doesn't toggle the section open/closed. */}
        <span onClick={(e) => e.stopPropagation()} className="ml-1">
          <button onClick={() => {
              const ok = downloadSVG(mainChartRef.current, `${fileBase}.svg`)
              if (ok) toast.success('Exported SVG')
              else    toast.error('Chart not ready')
            }}
            className="flex items-center gap-1 text-[11px] text-muted hover:text-accent">
            <Download size={11} /> SVG
          </button>
        </span>
        <button onClick={(e) => { e.stopPropagation(); exportCSV() }}
          className="flex items-center gap-1 text-[11px] text-muted hover:text-accent ml-1"
        ><Download size={11} /> CSV</button>
      </header>
      {open && (
        <div className="px-2 py-2">
          {emptyHint ? (
            <div className="text-[11px] text-muted text-center py-3 italic px-3">{emptyHint}</div>
          ) : !agg ? (
            <div className="text-[11px] text-muted text-center py-3 italic">
              No {groupKey.toLowerCase()} time-series available.
            </div>
          ) : (
          <div ref={mainChartRef}>
            {perAsset && perAssetView ? (
              <ResponsiveContainer width="100%" height={180}>
                <LineChart data={perAssetView.rows} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                  <CartesianGrid {...CHART_GRID} />
                  <XAxis dataKey="snapshot" tickFormatter={shortStamp} {...CHART_AXIS} />
                  <YAxis {...CHART_AXIS}
                    domain={showPct ? [0, 100] : undefined}
                    label={yAxisLabel(yUnit)} />
                  <RTooltip {...CHART_TOOLTIP} labelFormatter={shortStamp} formatter={(v: number) => `${v.toFixed(2)} ${yUnit}`} />
                  <Legend {...CHART_LEGEND} />
                  {perAssetView.cols.map((c, i) => (
                    <Line key={c} type="monotone" dataKey={c} stroke={TRACE_COLORS[i % TRACE_COLORS.length]}
                          strokeWidth={1.4} dot={false} />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <ResponsiveContainer width="100%" height={140}>
                {isStorage ? (
                  <LineChart data={aggView ?? agg} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                    <CartesianGrid {...CHART_GRID} />
                    <XAxis dataKey="snapshot" tickFormatter={shortStamp} {...CHART_AXIS} />
                    <YAxis {...CHART_AXIS}
                      domain={showPct ? [0, 100] : undefined}
                      label={yAxisLabel(yUnit)} />
                    <RTooltip {...CHART_TOOLTIP} labelFormatter={shortStamp} formatter={(v: number) => `${v.toFixed(2)} ${yUnit}`} />
                    <Line type="monotone" dataKey="value" stroke={style.color} strokeWidth={1.6} dot={false} />
                  </LineChart>
                ) : (
                  <AreaChart data={aggView ?? agg} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                    <CartesianGrid {...CHART_GRID} />
                    <XAxis dataKey="snapshot" tickFormatter={shortStamp} {...CHART_AXIS} />
                    <YAxis {...CHART_AXIS} label={yAxisLabel(yUnit)} />
                    <RTooltip {...CHART_TOOLTIP} labelFormatter={shortStamp} formatter={(v: number) => `${v.toFixed(2)} ${yUnit}`} />
                    <Area type="monotone" dataKey="value" stroke={style.color} fill={style.color} fillOpacity={0.18} strokeWidth={1.6} />
                  </AreaChart>
                )}
              </ResponsiveContainer>
            )}
          </div>
          )}

          {/* Optional curtailment sibling chart (Renewables only). Sits below
              the main dispatch chart with the same X-axis range so the user
              can read off the curtailed MW at any timestamp. Amber colour
              distinguishes it from the green dispatch trace above. */}
          {showCurtailment && curtailmentSeries && (
            <div className="mt-2 pt-2 border-t border-border/60">
              <div className="flex items-center gap-2 px-1 pb-1 text-[10px] text-muted">
                <Scissors size={10} className="text-amber-600" />
                <span className="font-medium text-amber-700">Curtailment</span>
                <span>MW spilled per snapshot · Σ {fmtEnergy(
                  curtailmentSeries.reduce((a, r) => a + r.value, 0))}
                </span>
                <span className="ml-auto flex items-center gap-2">
                  <button onClick={() => {
                      const ok = downloadSVG(curtailChartRef.current, `${fileBase}_curtailment.svg`)
                      if (ok) toast.success('Exported SVG')
                      else    toast.error('Chart not ready')
                    }}
                    className="flex items-center gap-1 hover:text-accent">
                    <Download size={10} /> SVG
                  </button>
                  <button onClick={exportCurtailmentCSV}
                    className="flex items-center gap-1 hover:text-accent">
                    <Download size={10} /> CSV
                  </button>
                </span>
              </div>
              <div ref={curtailChartRef}>
                <ResponsiveContainer width="100%" height={120}>
                  <AreaChart data={curtailmentSeries} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                    <CartesianGrid {...CHART_GRID} />
                    <XAxis dataKey="snapshot" tickFormatter={shortStamp} {...CHART_AXIS} />
                    <YAxis {...CHART_AXIS} label={yAxisLabel('MW')} />
                    <RTooltip {...CHART_TOOLTIP} labelFormatter={shortStamp} formatter={(v: number) => `${v.toFixed(2)} MW`} />
                    <Area type="monotone" dataKey="value" stroke="#f59e0b" fill="#f59e0b" fillOpacity={0.22} strokeWidth={1.6} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          {/* Optional lost-load sibling chart (Load only). Mirrors the
              curtailment overlay above: same X axis as the main chart, MW
              y-axis, danger-red shading so it visually distinguishes from the
              orange load trace. Header reports Σ MWh + total VOLL cost so
              users see the reliability gap inline. */}
          {showLostLoad && lostLoadSeries && (
            <div className="mt-2 pt-2 border-t border-border/60">
              <div className="flex items-center gap-2 px-1 pb-1 text-[10px] text-muted">
                <Zap size={10} className="text-danger" />
                <span className="font-medium text-danger">Lost load</span>
                <span>
                  MW unmet per snapshot
                  {lostLoadTotals && ` · Σ ${fmtEnergy(lostLoadTotals.mwh)} · ${fmtCurrency(lostLoadTotals.cost)}`}
                </span>
                <span className="ml-auto flex items-center gap-2">
                  <button onClick={() => {
                      const ok = downloadSVG(lostLoadChartRef.current, `${fileBase}_lost_load.svg`)
                      if (ok) toast.success('Exported SVG')
                      else    toast.error('Chart not ready')
                    }}
                    className="flex items-center gap-1 hover:text-accent">
                    <Download size={10} /> SVG
                  </button>
                  <button onClick={exportLostLoadCSV}
                    className="flex items-center gap-1 hover:text-accent">
                    <Download size={10} /> CSV
                  </button>
                </span>
              </div>
              <div ref={lostLoadChartRef}>
                <ResponsiveContainer width="100%" height={120}>
                  <AreaChart data={lostLoadSeries} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                    <CartesianGrid {...CHART_GRID} />
                    <XAxis dataKey="snapshot" tickFormatter={shortStamp} {...CHART_AXIS} />
                    <YAxis {...CHART_AXIS} label={yAxisLabel('MW')} />
                    <RTooltip {...CHART_TOOLTIP} labelFormatter={shortStamp} formatter={(v: number) => `${v.toFixed(2)} MW`} />
                    <Area type="monotone" dataKey="value" stroke="#dc2626" fill="#dc2626" fillOpacity={0.22} strokeWidth={1.6} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  )
}

// ── Full Load Hours evaluation ───────────────────────────────────────────────
// Per-asset Full Load Hours (FLH) table. For every Generator, Storage Unit,
// and Link in the network we compute:
//
//   FLH = Σ |p|_t × snapshot_weight × period_year_weight  /  p_nom_opt
//
// The numerator is the weighted dispatch energy in MWh — same scaling basis
// `n.statistics()['Supply']` uses (snapshot_weightings.generators ×
// investment_period_weightings.years). For storage we only count the
// DISCHARGE half (positive p) because charge energy isn't "delivered" energy.
// For links we take |p0| because both forward and reverse flows count as
// utilisation of the link's capacity.
//
// Capacity (denominator):
//   • No period filter (aggregated view): use the asset's final cumulative
//     p_nom_opt from /api/network/{component} (PyPSA's solved value).
//   • A specific period selected via the strip: walk the vintage results to
//     compute the period-active capacity:
//       cap_at_P = initial_capacity + Σ vintage.p_nom_opt where build_year ≤ P
//     This makes FLH read correctly in early periods where not all vintages
//     have been built yet.
//
// Rows with capacity ≤ 0 (e.g. extendable assets that the LP decided not to
// build) are still shown — energy and FLH render as "—" so the user can see
// which assets the optimiser passed on.

type FlhKind = 'Generator' | 'Storage' | 'Link'
interface FlhRow {
  name: string
  kind: FlhKind
  category: string  // refined bucket: Thermal / Renewable / Battery / Hydrogen / Heat / Electrolyzer / carrier
  carrier: string
  pNom: number
  energy: number
  flh: number
}

interface VintageResultsShape {
  results: Record<string, Record<string, {
    capacity_field: string
    initial_capacity: number
    periods: Array<{ build_year: number; p_nom_opt: number; p_nom_min: number; p_nom_max: number | null }>
  }>>
}

function vintageCapAtPeriod(
  vr: VintageResultsShape | null | undefined,
  componentClass: 'Generator' | 'StorageUnit' | 'Link' | 'Store',
  assetName: string,
  period: number,
): number | null {
  if (!vr) return null
  const compMap = vr.results?.[componentClass]
  if (!compMap) return null
  const entry = compMap[assetName]
  if (!entry) return null
  let cap = entry.initial_capacity ?? 0
  for (const v of entry.periods ?? []) {
    if (v.build_year <= period) cap += v.p_nom_opt ?? 0
  }
  return Number.isFinite(cap) ? cap : null
}

function FullLoadHoursSection(props: {
  generators: Generator[]
  storageUnits: StorageUnit[]
  links: LinkT[]
  gensTS: TSPayload | null
  storPowerTS: TSPayload | null
  linkTS: TSPayload | null
  weightCtx: WeightCtx
  range: { from: number; to: number }
  selectedPeriod: number | string | null
  electrolyzerNames: Set<string>
  vintageResults?: VintageResultsShape | null
  scaled: boolean
  avgScale: number
}) {
  const {
    generators, storageUnits, links,
    gensTS, storPowerTS, linkTS,
    weightCtx, range, selectedPeriod, electrolyzerNames,
    vintageResults, scaled, avgScale,
  } = props

  // Per-column per-period weighted energy. Returns Map<period, MWh/year> for
  // one asset (column).
  //
  // Apply snapshot_weightings.generators ONLY — NOT the investment-period
  // `years` multiplier. FLH is a per-year quantity (hours/year), so the
  // numerator must be MWh per ONE solved year, not the period's lifetime-
  // equivalent. (`weightedSum` from ./shared multiplies by ipw.years which
  // is wrong for FLH.)
  //
  // 'positive': clamp to ≥ 0 (used for generators & storage discharge)
  // 'absolute': |p| (used for links — both directions are utilisation)
  function perColumnPerPeriodEnergy(
    ts: TSPayload | null,
    name: string,
    mode: 'positive' | 'absolute',
  ): Map<number | string | null, number> {
    const out = new Map<number | string | null, number>()
    if (!ts) return out
    const colIdx = ts.columns.indexOf(name)
    if (colIdx < 0) return out
    const lastRow = ts.data.length - 1
    const rFrom = Math.max(0, Math.min(range.from, lastRow))
    const rTo   = Math.max(0, Math.min(range.to,   lastRow))
    if (rFrom > rTo) return out
    const sw = weightCtx.snapshotWeights ?? []
    const periods = ts.periods ?? weightCtx.snapshotPeriods ?? []
    for (let row = rFrom; row <= rTo; row++) {
      const raw = ts.data[row][colIdx]
      if (!Number.isFinite(raw)) continue
      let val = raw
      if (mode === 'positive') {
        if (val < 0) continue
      } else {
        val = Math.abs(val)
      }
      // Snapshot weight only (energy-side scaling) — NO ipw.years here.
      let w = 1
      if (sw.length > 0) {
        const swr = sw[row]
        if (swr) {
          const v = swr.generators
          if (typeof v === 'number' && Number.isFinite(v)) w = v
        }
      }
      const p = periods[row] ?? null
      out.set(p, (out.get(p) ?? 0) + val * w)
    }
    return out
  }

  // Resolve per-period FLH for one asset and reduce to display values.
  //
  //   • Period filter active  → values for that period only.
  //   • Aggregated (no filter) → arithmetic mean across periods where the
  //     asset had non-zero capacity (so a vintage that wasn't built yet in
  //     period 2026 doesn't drag the average to zero).
  //
  // Returns { pNom, energy, flh } as the display values, with capacity
  // resolved via vintageResults when available (period-active) or the
  // final cumulative p_nom_opt otherwise.
  function flhForAsset(
    ts: TSPayload | null,
    name: string,
    componentClass: 'Generator' | 'StorageUnit' | 'Link',
    mode: 'positive' | 'absolute',
    finalPNom: number,
  ): { pNom: number; energy: number; flh: number } {
    const perPeriodEnergy = perColumnPerPeriodEnergy(ts, name, mode)

    // Period filter active — return that single slice.
    if (selectedPeriod != null && typeof selectedPeriod === 'number') {
      const energy = perPeriodEnergy.get(selectedPeriod) ?? 0
      const capRaw = vintageCapAtPeriod(vintageResults, componentClass, name, selectedPeriod)
      const pNom = (capRaw != null && capRaw > 0) ? capRaw : finalPNom
      return { pNom, energy, flh: pNom > 0 ? energy / pNom : 0 }
    }

    // Aggregated — iterate the SET of periods present in the snapshot
    // index (not just the periods in perPeriodEnergy). Two bugs the
    // earlier version had:
    //
    //   1. Bias-high: a period where the asset had capacity but the LP
    //      chose zero dispatch (battery that didn't cycle, generator
    //      that wasn't merit-ordered in) had no entry in
    //      perPeriodEnergy, so the loop skipped it. Iterating periodSet
    //      and falling back to `energy = 0` for missing entries fixes
    //      that — those periods now count toward the average at FLH=0.
    //
    //   2. Inconsistent FLH: the displayed `flh` was an arithmetic
    //      mean of per-period ratios `Σ (energy_p / cap_p) / N`, while
    //      the category bar chart (and the user's mental model) uses
    //      capacity-weighted `Σ energy_p / Σ cap_p`. Same view, two
    //      different numbers. Fixed by using the capacity-weighted
    //      formula here too — the displayed pNom and energy are still
    //      arithmetic means, but the identity `flh = energy / pNom`
    //      still holds because both reduce to sumEnergy/sumCap when
    //      the nActive denominators cancel.
    const periodSet = new Set<number | string | null>()
    const lastRow = (ts?.data.length ?? 1) - 1
    const rFrom = Math.max(0, Math.min(range.from, lastRow))
    const rTo = Math.max(0, Math.min(range.to, lastRow))
    if (ts && ts.periods && ts.periods.length > 0) {
      for (let row = rFrom; row <= rTo; row++) {
        periodSet.add(ts.periods[row] ?? null)
      }
    } else {
      // Flat snapshots — single bucket keyed by null.
      periodSet.add(null)
    }

    let sumEnergy = 0
    let sumCap = 0
    let nActive = 0
    for (const p of periodSet) {
      const energy = perPeriodEnergy.get(p) ?? 0
      let cap = finalPNom
      if (typeof p === 'number') {
        const v = vintageCapAtPeriod(vintageResults, componentClass, name, p)
        if (v != null && v > 0) cap = v
      }
      if (cap <= 0) continue
      sumEnergy += energy
      sumCap += cap
      nActive += 1
    }
    if (nActive === 0 || sumCap <= 0) {
      return { pNom: finalPNom, energy: 0, flh: 0 }
    }
    return {
      pNom: sumCap / nActive,
      energy: sumEnergy / nActive,
      flh: sumEnergy / sumCap,
    }
  }

  const rows: FlhRow[] = useMemo(() => {
    const out: FlhRow[] = []
    // Generators — Σ p × snap_w per period. Default is 'positive' so
    // curtailment excursions don't count as utilisation. Sink generators
    // (heat dumps, spill, ...) have p_max_pu ≤ 0 which means dispatch is
    // ALWAYS non-positive — under 'positive' mode every snapshot is
    // skipped and FLH reads as 0. Switch those to 'absolute' so |p| is
    // counted, matching the user's mental model of "how often was this
    // sink operating".
    for (const g of generators) {
      const carrier = (g as unknown as { carrier?: string }).carrier ?? ''
      const category = generatorGroup(carrier)  // 'Thermal' | 'Renewable'
      const finalPNom = Number(
        (g as unknown as { p_nom_opt?: number; p_nom?: number }).p_nom_opt
        ?? (g as unknown as { p_nom?: number }).p_nom
        ?? 0,
      )
      const pMaxPu = Number((g as unknown as { p_max_pu?: number }).p_max_pu ?? 1)
      const isSink = Number.isFinite(pMaxPu) && pMaxPu <= 0
      const { pNom, energy, flh } = flhForAsset(
        gensTS, g.name, 'Generator', isSink ? 'absolute' : 'positive', finalPNom,
      )
      out.push({ name: g.name, kind: 'Generator', category, carrier: carrier || '—', pNom, energy, flh })
    }
    // Storage units — discharge-only (positive p). p_nom_opt is the
    // discharge capacity. For storage there's a meaningful "cycles" view
    // (Storage Cycling tab) — FLH here is the dispatch-side analogue.
    for (const s of storageUnits) {
      const carrier = (s as unknown as { carrier?: string }).carrier ?? ''
      const category = `Storage · ${storageCarrierLabel(storageCarrierKey(carrier))}`
      const finalPNom = Number(
        (s as unknown as { p_nom_opt?: number; p_nom?: number }).p_nom_opt
        ?? (s as unknown as { p_nom?: number }).p_nom
        ?? 0,
      )
      const { pNom, energy, flh } = flhForAsset(storPowerTS, s.name, 'StorageUnit', 'positive', finalPNom)
      out.push({ name: s.name, kind: 'Storage', category, carrier: carrier || '—', pNom, energy, flh })
    }
    // Links — Σ |p0| × snap_w per period (both directions count as
    // utilisation). Electrolyzer links get their own category so the
    // H2-production fleet's FLH reads separately from HVDC FLH.
    for (const k of links) {
      const carrier = (k as unknown as { carrier?: string }).carrier ?? ''
      const isElec = electrolyzerNames.has(k.name)
      const category = isElec
        ? 'Electrolyzer'
        : `Link · ${carrier || 'unspecified'}`
      const finalPNom = Number(
        (k as unknown as { p_nom_opt?: number; p_nom?: number }).p_nom_opt
        ?? (k as unknown as { p_nom?: number }).p_nom
        ?? 0,
      )
      const { pNom, energy, flh } = flhForAsset(linkTS, k.name, 'Link', 'absolute', finalPNom)
      out.push({ name: k.name, kind: 'Link', category, carrier: carrier || '—', pNom, energy, flh })
    }
    return out
  }, [generators, storageUnits, links, gensTS, storPowerTS, linkTS,
      electrolyzerNames, vintageResults, selectedPeriod,
      weightCtx, range.from, range.to])

  // Capacity-weighted FLH per category — a fleet operating 4000 h on 500 MW
  // is more "representative" of utilisation than one tiny 50 MW unit at
  // 8000 h. Aggregates per-asset MEANS (each asset's r.energy is its mean
  // across active periods, each r.pNom is its mean capacity across active
  // periods); category flh = (Σ_a mean_energy_a) / (Σ_a mean_cap_a). When
  // all assets are active in the same number of periods this collapses to
  // the true Σ_a Σ_p energy_ap / Σ_a Σ_p cap_ap. When some vintages are
  // built late, late assets weigh less in the fleet number — which matches
  // the user's intuition that a vintage built in the last period
  // shouldn't dominate the horizon-wide FLH.
  const categoryStats = useMemo(() => {
    const groups = new Map<string, { energy: number; cap: number; count: number }>()
    for (const r of rows) {
      const g = groups.get(r.category) ?? { energy: 0, cap: 0, count: 0 }
      g.energy += r.energy
      g.cap += r.pNom
      g.count += 1
      groups.set(r.category, g)
    }
    return Array.from(groups.entries())
      .map(([category, g]) => ({
        category,
        count: g.count,
        capacity: g.cap,
        energy: g.energy,
        flh: g.cap > 0 ? g.energy / g.cap : 0,
      }))
      .sort((a, b) => b.flh - a.flh)
  }, [rows])

  const scopeLabel = selectedPeriod != null
    ? `period ${selectedPeriod}`
    : 'avg across periods'

  if (rows.length === 0) {
    return (
      <div className="p-6 text-[12px] text-muted">
        No assets to evaluate. Add at least one generator, storage unit, or link to the network.
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <header>
        <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em] flex items-center gap-2">
          Full load hours
          <span className="text-[10px] font-normal text-muted">{scopeLabel}</span>
          {scaled && (
            <span className="text-[10px] font-normal text-accent bg-accent/10 px-1.5 py-0.5 rounded font-mono"
              title={`Energy uses snapshot_weightings.generators (annualised per-year MWh) — no investment_period_weightings.years multiplier, so FLH stays a per-year quantity. Average snapshot-weighting factor across the selected horizon: ${avgScale.toFixed(2)}.`}>
              × {avgScale.toFixed(2)} weighted
            </span>
          )}
        </h3>
        <p className="text-[11px] text-muted mt-1">
          FLH<sub>p</sub> = Σ |p|<sub>t∈p</sub> × snapshot_weight ÷ capacity<sub>p</sub> — equivalent
          hours at nameplate, computed PER PERIOD. {selectedPeriod != null
            ? 'Showing the selected period only.'
            : 'Aggregated view shows the arithmetic mean of FLH across all periods where the asset had non-zero capacity (so vintages that aren’t built yet don’t drag the average to zero).'}
          {' '}Generator FLH uses dispatch p (≥ 0); storage FLH counts discharge only (positive p);
          link FLH counts |p0| in both directions. Capacity per period comes from vintage results
          (cumulative built by that period) and falls back to the final p_nom_opt.
        </p>
      </header>

      {/* ── Per-category bar chart ───────────────────────────────── */}
      <section className="rounded-[10px] border border-border bg-bg overflow-hidden">
        <header className="px-4 py-2.5 border-b border-border bg-bg-2 flex items-center gap-2">
          <span className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">By category</span>
          <span className="text-[10px] text-muted">
            {selectedPeriod != null
              ? 'capacity-weighted FLH for the selected period'
              : 'capacity-weighted FLH (per-asset averages across periods then summed)'}
          </span>
          <span className="flex-1" />
          <button
            onClick={() => {
              downloadCSV('results_flh_by_category.csv',
                ['category', 'asset_count', 'capacity_MW', 'energy_MWh_per_year', 'flh_hours_per_year'],
                categoryStats.map(c => [c.category, c.count, c.capacity, c.energy, c.flh] as [string, number, number, number, number]),
              )
              toast.success('Exported')
            }}
            className="flex items-center gap-1 text-[11px] text-muted hover:text-accent"
          ><Download size={11} /> CSV</button>
        </header>
        <div className="p-3">
          <ResponsiveContainer width="100%" height={220}>
            <ComposedChart data={categoryStats} margin={{ top: 4, right: 12, left: 0, bottom: 4 }}>
              <CartesianGrid {...CHART_GRID} />
              <XAxis dataKey="category" {...CHART_AXIS} interval={0}
                angle={categoryStats.length > 5 ? -15 : 0}
                textAnchor={categoryStats.length > 5 ? 'end' : 'middle'}
                height={categoryStats.length > 5 ? 50 : 30} />
              <YAxis {...CHART_AXIS} label={yAxisLabel('FLH (h/year)')} />
              <RTooltip {...CHART_TOOLTIP}
                formatter={(v: number, n: string) => {
                  if (n === 'flh') return [`${v.toFixed(0)} h/year`, 'FLH']
                  return [v.toFixed(0), n]
                }} />
              <Area type="monotone" dataKey="flh" fill="#0ea5e9" stroke="#0ea5e9" fillOpacity={0.5} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </section>

      {/* ── Per-asset filterable table ────────────────────────────── */}
      <PerAssetFlhTable rows={rows} scopeLabel={scopeLabel} />
    </div>
  )
}

function PerAssetFlhTable({ rows, scopeLabel }: { rows: FlhRow[]; scopeLabel: string }) {
  type K = 'name' | 'kind' | 'category' | 'carrier' | 'pNom' | 'energy' | 'flh'
  const ft = useFilterableTable<FlhRow, K>({
    rows,
    initialSortKey: 'flh',
    initialSortDir: 'desc',
    getSearchText: r => [r.name, r.kind, r.category, r.carrier].join(' ').toLowerCase(),
    getValue: (r, k) =>
      (k === 'name' || k === 'kind' || k === 'category' || k === 'carrier')
        ? r[k]
        : r[k] as number,
  })
  return (
    <section className="rounded-[10px] border border-border bg-bg overflow-hidden">
      <header className="px-4 py-2.5 border-b border-border bg-bg-2 flex items-center gap-2 flex-wrap">
        <span className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">Per-asset detail</span>
        <span className="text-[10px] text-muted">{scopeLabel} · click headers to sort</span>
        <span className="flex-1" />
        <TableSearchBox value={ft.search} onChange={ft.setSearch} placeholder="Filter…" />
        <button
          onClick={() => {
            downloadCSV('results_flh_per_asset.csv',
              ['name', 'kind', 'category', 'carrier', 'p_nom_MW', 'energy_MWh_per_year', 'flh_hours_per_year'],
              ft.rows.map(r => [r.name, r.kind, r.category, r.carrier, r.pNom, r.energy, r.flh] as [string, string, string, string, number, number, number]),
            )
            toast.success('Exported')
          }}
          className="flex items-center gap-1 text-[11px] text-muted hover:text-accent"
        ><Download size={11} /> CSV</button>
      </header>
      <div className="p-3 overflow-x-auto">
        <table className="w-full text-[11.5px]">
          <thead>
            <tr className="border-b border-border text-muted">
              <SortHeader columnKey="name"     label="Asset"        sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('name')} />
              <SortHeader columnKey="kind"     label="Kind"         sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('kind')} />
              <SortHeader columnKey="category" label="Category"     sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('category')} />
              <SortHeader columnKey="carrier"  label="Carrier"      sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('carrier')} />
              <SortHeader columnKey="pNom"     label="p_nom (MW)"     sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('pNom')}   align="right" />
              <SortHeader columnKey="energy"   label="Energy (MWh/y)" sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('energy')} align="right" />
              <SortHeader columnKey="flh"      label="FLH (h/y)"      sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('flh')}    align="right" />
            </tr>
          </thead>
          <tbody>
            {ft.rows.map(r => (
              <tr key={`${r.kind}:${r.name}`} className="border-b border-border/40">
                <td className="py-1 text-text font-mono">{r.name}</td>
                <td className="py-1 text-muted">{r.kind}</td>
                <td className="py-1 text-muted">{r.category}</td>
                <td className="py-1 text-muted">{r.carrier}</td>
                <td className="py-1 text-right font-mono text-text">{r.pNom.toFixed(1)}</td>
                <td className="py-1 text-right font-mono text-muted">
                  {r.pNom > 0 ? r.energy.toFixed(0) : '—'}
                </td>
                <td className="py-1 text-right font-mono text-text">
                  {r.pNom > 0 ? r.flh.toFixed(0) : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
