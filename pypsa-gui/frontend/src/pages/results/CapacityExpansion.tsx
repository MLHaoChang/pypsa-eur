import { useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip as RTooltip, Cell, Legend,
} from 'recharts'
import { Download } from 'lucide-react'
import toast from 'react-hot-toast'
import { resultsApi, simulationApi } from '../../api/simulation'
import { networkApi } from '../../api/network'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import type { Generator, Line as LineT, Link as LinkT, StorageUnit, Store, Transformer } from '../../api/types'
import { fmtCurrency, fmtPower, fmtEnergy, downloadCSV, KPI, isRenewableCarrier, ChartActions,
  ChartCard, Seg, CHART_GRID, CHART_AXIS, CHART_TOOLTIP, CHART_LEGEND, yAxisLabel } from './shared'
import { useResultsFilter } from './filterContext'
import { useFilterableTable, TableSearchBox, SortHeader } from './useFilterableTable'
import { CarrierFilter, useCarrierFilter, bindCarrierFilter } from './CarrierFilter'
import { canonicaliseCarrier, type ComponentClass } from './carrierAliases'

// ── Capacity Expansion tab ────────────────────────────────────────────────────
// Cost summary (KPIs + CAPEX-by-class chart) sits at the top. Below it, one
// table PER component class — generators (with Renewable/Conventional split),
// lines, transformers, storage units, stores, links — each showing the columns
// most useful for that class:
//   • build_year tells you WHEN the optimiser placed the investment (relevant
//     for myopic / perfect-foresight multi-period runs).
//   • length only matters for Lines.
//   • Stores measure capacity in MWh (energy), Storage Units in MW (power) +
//     max_hours → MWh.
// Each subsection auto-hides when its asset list is empty so a single-carrier
// network doesn't show 5 "No expansion" cards.

const COMPONENT_COLOR: Record<string, string> = {
  Generator:    '#16a34a',
  StorageUnit:  '#7c3aed',
  Store:        '#a855f7',
  Link:         '#0ea5e9',
  Line:         '#dc2626',
  Transformer:  '#d97706',
}

// PyPSA stores most extendability attrs (build_year, p_nom_opt, …) on the
// component DataFrame; our TS types omit a few of them. Cast through unknown
// to read them without flooding every getter with `as { build_year?: number }`.
function attr<T = number>(obj: object, key: string): T | undefined {
  return (obj as unknown as Record<string, T | undefined>)[key]
}
function buildYear(obj: object): number | '' {
  const v = attr<number>(obj, 'build_year')
  return v == null || !Number.isFinite(v) ? '' : v
}

interface SizedRowBase {
  name: string
  carrier?: string
  initial: number
  optimal: number
  delta: number
  capital_cost: number
  capex: number
  build_year: number | ''
}
interface SizedLineRow extends SizedRowBase { length: number }
interface SizedStorageRow extends SizedRowBase { max_hours: number; optimal_energy: number }

// Retirements are the mirror: same shape but instead of (delta, capex) we
// track (retired, savings) — both stored as positive numbers so the table
// labels read naturally without negatives. Optimal is still the final
// capacity; initial - retired ≈ optimal.
interface RetiredRowBase {
  name: string
  carrier?: string
  initial: number
  optimal: number
  retired: number       // |delta|, positive
  capital_cost: number
  savings: number       // capital_cost × retired
  build_year: number | ''
}
interface RetiredLineRow extends RetiredRowBase { length: number }
interface RetiredStorageRow extends RetiredRowBase { max_hours: number; retired_energy: number }

export default function CapacityExpansion() {
  const currentProject = useUIStore(s => s.currentProject)
  const filter = useResultsFilter()
  const capexChartRef = useRef<HTMLDivElement | null>(null)
  const waterfallChartRef = useRef<HTMLDivElement | null>(null)
  const perPeriodCapRef = useRef<HTMLDivElement | null>(null)
  const perPeriodClassRef = useRef<HTMLDivElement | null>(null)
  const { data: cost, isLoading } = useQuery({
    queryKey: nk(currentProject, 'results', 'cost_breakdown'),
    queryFn: resultsApi.getCostBreakdown,
  })

  const { data: generators = [] }   = useQuery({ queryKey: nk(currentProject, 'generators'),    queryFn: networkApi.getGenerators })
  const { data: storageUnits = [] } = useQuery({ queryKey: nk(currentProject, 'storage_units'), queryFn: networkApi.getStorageUnits })
  const { data: stores = [] }       = useQuery({ queryKey: nk(currentProject, 'stores'),        queryFn: networkApi.getStores })
  const { data: links = [] }        = useQuery({ queryKey: nk(currentProject, 'links'),         queryFn: networkApi.getLinks })
  const { data: lines = [] }        = useQuery({ queryKey: nk(currentProject, 'lines'),         queryFn: networkApi.getLines })
  const { data: transformers = [] } = useQuery({ queryKey: nk(currentProject, 'transformers'),  queryFn: networkApi.getTransformers })

  // Per-vintage breakdown — populated by the backend when an asset's
  // per-period bounds were applied at the last solve. Lets the table emit one
  // row per (asset, build_year) tuple, so a battery built across 2026 / 2027 /
  // 2028 reports those three rows individually instead of collapsing to the
  // parent's single (pre-expansion) build_year.
  const { data: vintageResults } = useQuery({
    queryKey: nk(currentProject, 'vintage_results'),
    queryFn: networkApi.listVintageResults,
  })
  const vintagesFor = (componentClass: string, name: string) => {
    const periods = vintageResults?.results?.[componentClass]?.[name]?.periods
    if (!periods || periods.length === 0) return null
    return periods
  }
  // Existing (pre-multi-period) capacity carried on the parent before
  // vintage expansion. Each per-vintage row's `initial` accumulates this
  // base plus every earlier vintage's build, so the table reads as a
  // chronological "what was already installed before this period".
  const initialCapacityFor = (componentClass: string, name: string): number =>
    vintageResults?.results?.[componentClass]?.[name]?.initial_capacity ?? 0
  // Cross-row sort: build_year ascending (chronological — earliest vintages
  // first). Non-vintage assets carry their original build_year too, so the
  // mixed sort still makes sense. Ties fall back to capex DESC so the
  // biggest investment within a year stays at the top.
  type SortableRow = { build_year: number | ''; capex: number }
  const sortByBuildYear = <T extends SortableRow>(a: T, b: T) => {
    const ay = typeof a.build_year === 'number' ? a.build_year : Number.POSITIVE_INFINITY
    const by = typeof b.build_year === 'number' ? b.build_year : Number.POSITIVE_INFINITY
    if (ay !== by) return ay - by
    return b.capex - a.capex
  }

  // Periodized per-asset CAPEX from PyPSA's own annuity math — fills in the
  // gap when an asset has capital_cost=0 but overnight_cost>0. Without this
  // the tables below show €0 for any battery / line / etc. parameterised
  // via overnight_cost alone, which was the source of the
  // "Why is the investment 0?" confusion in the original UI.
  // Defaults to an empty object so the lookups below (and the .map calls
  // on `Object.keys(...)`) stay safe before the query resolves.
  const { data: assetCosts = {} } = useQuery({
    queryKey: ['asset_costs'],
    queryFn: simulationApi.getAssetCosts,
  })
  // ── CAPEX display mode ──────────────────────────────────────────────────
  // "Annualised"        — €/MW/yr from PyPSA's periodized cost (LP value).
  // "Total investment"  — present-value of the upfront overnight investment.
  //                       Future-year builds get discounted back to the
  //                       reference year (= min build_year) at each asset's
  //                       own discount_rate. For year-0 builds the PV
  //                       factor is 1, so the displayed number matches the
  //                       nominal overnight × Δcapacity that users expect.
  const [costMode, setCostMode] = useState<'annual' | 'lifetime'>('annual')
  // Per-asset cost lookup. In lifetime mode we return the PV-adjusted
  // overnight value so the table / chart / KPIs all show today's-money
  // capex for assets the optimiser placed in future years.
  const costPerUnit = (comp: string, name: string, raw: number): number => {
    const m = assetCosts[comp]?.[name]
    if (!m) return raw
    const v = costMode === 'lifetime' ? m.overnight_cost_pv : m.capital_cost
    return v != null && Number.isFinite(v) && v > 0 ? v : raw
  }

  // Dedupe rows by (name, build_year) — keeps only the entry with the
  // highest CAPEX per key. Handles two pathological cases:
  //
  //   1) Race during initial render: `assetCosts` defaults to `{}` while
  //      its useQuery is still pending, so `costPerUnit` returns the raw
  //      `g.capital_cost` (often 0 for assets parameterised via
  //      `overnight_cost`). When `assetCosts` arrives, the memo re-runs
  //      and emits fresh rows with the proper periodized cost — but in
  //      some React-Query transition states the table briefly shows both
  //      copies side-by-side, producing "Solar2 2026 €0" alongside
  //      "Solar2 2026 €2.9M".
  //
  //   2) Vintage payload artefacts: if vintage_results ever ends up with
  //      a duplicate build_year entry (manual asset rename, save/load
  //      across schema changes), the unguarded flatMap would render both.
  //
  // The pickHighest dedupe is idempotent and cheap; it's preferable to
  // hard-suppressing the loading-state rows because waiting for ALL
  // queries to resolve would block the table render for the whole tab.
  function dedupeSizedRows<T extends { name: string; build_year: number | ''; capex: number }>(rows: T[]): T[] {
    const byKey = new Map<string, T>()
    for (const r of rows) {
      const k = `${r.name}@${r.build_year}`
      const prev = byKey.get(k)
      if (!prev || r.capex > prev.capex) byKey.set(k, r)
    }
    return Array.from(byKey.values())
  }

  // Per-class sized rows. Each filter follows the same pattern: extendable
  // assets where the optimiser actually grew the nominal capacity (delta>0).
  // Threshold of 1e-6 hides numerical-noise rows when the solver returns
  // p_nom_opt ≈ p_nom but not exactly equal.
  // flatMap with `[row]` / `[]` gives clean SizedRowBase[] inference without
  // needing an explicit type-predicate filter — the empty-array branch
  // already narrows out the no-row case.
  const sizedGens = useMemo<SizedRowBase[]>(() => {
    const out = (generators as Generator[]).flatMap<SizedRowBase>(g => {
      if (attr<boolean>(g, 'p_nom_extendable') !== true) return []
      const opt = attr<number>(g, 'p_nom_opt')
      const ini = g.p_nom ?? 0
      if (opt == null || !Number.isFinite(opt)) return []
      const cc = costPerUnit('generators', g.name, g.capital_cost ?? 0)
      const vintages = vintagesFor('Generator', g.name)
      if (vintages) {
        // One row per vintage build year. `initial` accumulates: it's the
        // parent's pre-existing capacity at the start of the EARLIEST
        // vintage, then each subsequent vintage's row picks up the running
        // total so far. `optimal` is the cumulative total INCLUDING this
        // vintage's build. delta = this vintage's contribution alone.
        let cumulative = initialCapacityFor('Generator', g.name)
        return vintages
          .filter(v => v.p_nom_opt > 1e-6)
          .map(v => {
            const initialRow = cumulative
            cumulative += v.p_nom_opt
            return {
              name: g.name, carrier: g.carrier ?? '',
              initial: initialRow, optimal: cumulative, delta: v.p_nom_opt,
              capital_cost: cc,
              capex: cc * v.p_nom_opt,
              build_year: v.build_year,
            }
          })
      }
      const delta = opt - ini
      if (delta <= 1e-6) return []
      return [{
        name: g.name, carrier: g.carrier ?? '',
        initial: ini, optimal: opt, delta,
        capital_cost: cc,
        capex: cc * delta,
        build_year: buildYear(g),
      }]
    })
    return dedupeSizedRows(out).sort(sortByBuildYear)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [generators, assetCosts, costMode, vintageResults])

  const sizedLines = useMemo<SizedLineRow[]>(() => {
    const out = (lines as LineT[]).flatMap<SizedLineRow>(l => {
      if (attr<boolean>(l, 's_nom_extendable') !== true) return []
      const opt = attr<number>(l, 's_nom_opt')
      const ini = l.s_nom ?? 0
      if (opt == null || !Number.isFinite(opt)) return []
      const cc = costPerUnit('lines', l.name, l.capital_cost ?? 0)
      const vintages = vintagesFor('Line', l.name)
      if (vintages) {
        let cumulative = initialCapacityFor('Line', l.name)
        return vintages
          .filter(v => v.p_nom_opt > 1e-6)
          .map(v => {
            const initialRow = cumulative
            cumulative += v.p_nom_opt
            return {
              name: l.name, carrier: l.carrier ?? '',
              initial: initialRow, optimal: cumulative, delta: v.p_nom_opt,
              capital_cost: cc,
              capex: cc * v.p_nom_opt,
              build_year: v.build_year,
              length: l.length ?? 0,
            }
          })
      }
      const delta = opt - ini
      if (delta <= 1e-6) return []
      return [{
        name: l.name, carrier: l.carrier ?? '',
        initial: ini, optimal: opt, delta,
        capital_cost: cc,
        capex: cc * delta,
        build_year: buildYear(l),
        length: l.length ?? 0,
      }]
    })
    return dedupeSizedRows(out).sort(sortByBuildYear)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lines, assetCosts, costMode, vintageResults])

  const sizedTransformers = useMemo<SizedRowBase[]>(() => {
    const out = (transformers as Transformer[]).flatMap<SizedRowBase>(t => {
      if (attr<boolean>(t, 's_nom_extendable') !== true) return []
      const opt = attr<number>(t, 's_nom_opt')
      const ini = t.s_nom ?? 0
      if (opt == null || !Number.isFinite(opt)) return []
      const cc = costPerUnit('transformers', t.name, t.capital_cost ?? 0)
      const vintages = vintagesFor('Transformer', t.name)
      if (vintages) {
        let cumulative = initialCapacityFor('Transformer', t.name)
        return vintages
          .filter(v => v.p_nom_opt > 1e-6)
          .map(v => {
            const initialRow = cumulative
            cumulative += v.p_nom_opt
            return {
              name: t.name,
              initial: initialRow, optimal: cumulative, delta: v.p_nom_opt,
              capital_cost: cc,
              capex: cc * v.p_nom_opt,
              build_year: v.build_year,
            }
          })
      }
      const delta = opt - ini
      if (delta <= 1e-6) return []
      return [{
        name: t.name,
        initial: ini, optimal: opt, delta,
        capital_cost: cc,
        capex: cc * delta,
        build_year: buildYear(t),
      }]
    })
    return dedupeSizedRows(out).sort(sortByBuildYear)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [transformers, assetCosts, costMode, vintageResults])

  const sizedStorage = useMemo<SizedStorageRow[]>(() => {
    const out = (storageUnits as StorageUnit[]).flatMap<SizedStorageRow>(s => {
      if (attr<boolean>(s, 'p_nom_extendable') !== true) return []
      const opt = attr<number>(s, 'p_nom_opt')
      const ini = s.p_nom ?? 0
      if (opt == null || !Number.isFinite(opt)) return []
      const hours = s.max_hours ?? 0
      const cc = costPerUnit('storage_units', s.name, s.capital_cost ?? 0)
      const vintages = vintagesFor('StorageUnit', s.name)
      if (vintages) {
        let cumulative = initialCapacityFor('StorageUnit', s.name)
        return vintages
          .filter(v => v.p_nom_opt > 1e-6)
          .map(v => {
            const initialRow = cumulative
            cumulative += v.p_nom_opt
            return {
              name: s.name, carrier: s.carrier ?? '',
              initial: initialRow, optimal: cumulative, delta: v.p_nom_opt,
              capital_cost: cc,
              capex: cc * v.p_nom_opt,
              build_year: v.build_year,
              max_hours: hours,
              optimal_energy: cumulative * hours,
            }
          })
      }
      const delta = opt - ini
      if (delta <= 1e-6) return []
      return [{
        name: s.name, carrier: s.carrier ?? '',
        initial: ini, optimal: opt, delta,
        capital_cost: cc,
        capex: cc * delta,
        build_year: buildYear(s),
        max_hours: hours,
        optimal_energy: opt * hours,
      }]
    })
    return dedupeSizedRows(out).sort(sortByBuildYear)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storageUnits, assetCosts, costMode, vintageResults])

  const sizedStores = useMemo<SizedRowBase[]>(() => {
    const out = (stores as Store[]).flatMap<SizedRowBase>(s => {
      if (attr<boolean>(s, 'e_nom_extendable') !== true) return []
      const opt = attr<number>(s, 'e_nom_opt')
      const ini = (s as unknown as { e_nom?: number }).e_nom ?? 0
      if (opt == null || !Number.isFinite(opt)) return []
      const cc = costPerUnit('stores', s.name, s.capital_cost ?? 0)
      const vintages = vintagesFor('Store', s.name)
      if (vintages) {
        let cumulative = initialCapacityFor('Store', s.name)
        return vintages
          .filter(v => v.p_nom_opt > 1e-6)
          .map(v => {
            const initialRow = cumulative
            cumulative += v.p_nom_opt
            return {
              name: s.name, carrier: s.carrier ?? '',
              initial: initialRow, optimal: cumulative, delta: v.p_nom_opt,
              capital_cost: cc,
              capex: cc * v.p_nom_opt,
              build_year: v.build_year,
            }
          })
      }
      const delta = opt - ini
      if (delta <= 1e-6) return []
      return [{
        name: s.name, carrier: s.carrier ?? '',
        initial: ini, optimal: opt, delta,
        capital_cost: cc,
        capex: cc * delta,
        build_year: buildYear(s),
      }]
    })
    return dedupeSizedRows(out).sort(sortByBuildYear)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stores, assetCosts, costMode, vintageResults])

  const sizedLinks = useMemo<SizedRowBase[]>(() => {
    const out = (links as LinkT[]).flatMap<SizedRowBase>(l => {
      if (attr<boolean>(l, 'p_nom_extendable') !== true) return []
      const opt = attr<number>(l, 'p_nom_opt')
      const ini = l.p_nom ?? 0
      if (opt == null || !Number.isFinite(opt)) return []
      const cc = costPerUnit('links', l.name, l.capital_cost ?? 0)
      const vintages = vintagesFor('Link', l.name)
      if (vintages) {
        let cumulative = initialCapacityFor('Link', l.name)
        return vintages
          .filter(v => v.p_nom_opt > 1e-6)
          .map(v => {
            const initialRow = cumulative
            cumulative += v.p_nom_opt
            return {
              name: l.name, carrier: l.carrier ?? '',
              initial: initialRow, optimal: cumulative, delta: v.p_nom_opt,
              capital_cost: cc,
              capex: cc * v.p_nom_opt,
              build_year: v.build_year,
            }
          })
      }
      const delta = opt - ini
      if (delta <= 1e-6) return []
      return [{
        name: l.name, carrier: l.carrier ?? '',
        initial: ini, optimal: opt, delta,
        capital_cost: cc,
        capex: cc * delta,
        build_year: buildYear(l),
      }]
    })
    return dedupeSizedRows(out).sort(sortByBuildYear)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [links, assetCosts, costMode, vintageResults])

  // (Carrier filter declared further down, after the retired-row useMemos
  // — keeps the topological order of references clean.)

  // ── Retired assets ─────────────────────────────────────────────────────
  // Mirror of the sized*-arrays but filtering for delta < 0 — i.e. capacity
  // the optimiser chose to RETIRE this run. A retirement is only possible
  // when (a) the asset is extendable and (b) p_nom_min is below p_nom. PyPSA
  // calls this "decommissioning" — the model can shrink the asset down to
  // its lower bound if the avoided capital_cost outweighs lost OPEX value.
  //
  // We store the absolute amount retired (|delta|) and the corresponding
  // savings (capital_cost × |delta|) so the table reads naturally ("retired
  // 200 MW, saved €50k/yr") without callers having to negate.
  const retiredGens = useMemo<RetiredRowBase[]>(() => {
    const out = (generators as Generator[]).flatMap<RetiredRowBase>(g => {
      if (attr<boolean>(g, 'p_nom_extendable') !== true) return []
      const opt = attr<number>(g, 'p_nom_opt')
      const ini = g.p_nom ?? 0
      if (opt == null || !Number.isFinite(opt)) return []
      const delta = opt - ini
      if (delta >= -1e-6) return []
      const retired = -delta
      const cc = costPerUnit('generators', g.name, g.capital_cost ?? 0)
      return [{
        name: g.name, carrier: g.carrier ?? '',
        initial: ini, optimal: opt, retired,
        capital_cost: cc,
        savings: cc * retired,
        build_year: buildYear(g),
      }]
    })
    return out.sort((a, b) => b.savings - a.savings)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [generators, assetCosts, costMode])

  const retiredLines = useMemo<RetiredLineRow[]>(() => {
    const out = (lines as LineT[]).flatMap<RetiredLineRow>(l => {
      if (attr<boolean>(l, 's_nom_extendable') !== true) return []
      const opt = attr<number>(l, 's_nom_opt')
      const ini = l.s_nom ?? 0
      if (opt == null || !Number.isFinite(opt)) return []
      const delta = opt - ini
      if (delta >= -1e-6) return []
      const retired = -delta
      const cc = costPerUnit('lines', l.name, l.capital_cost ?? 0)
      return [{
        name: l.name, carrier: l.carrier ?? '',
        initial: ini, optimal: opt, retired,
        capital_cost: cc,
        savings: cc * retired,
        build_year: buildYear(l),
        length: l.length ?? 0,
      }]
    })
    return out.sort((a, b) => b.savings - a.savings)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lines, assetCosts, costMode])

  const retiredTransformers = useMemo<RetiredRowBase[]>(() => {
    const out = (transformers as Transformer[]).flatMap<RetiredRowBase>(t => {
      if (attr<boolean>(t, 's_nom_extendable') !== true) return []
      const opt = attr<number>(t, 's_nom_opt')
      const ini = t.s_nom ?? 0
      if (opt == null || !Number.isFinite(opt)) return []
      const delta = opt - ini
      if (delta >= -1e-6) return []
      const retired = -delta
      const cc = costPerUnit('transformers', t.name, t.capital_cost ?? 0)
      return [{
        name: t.name,
        initial: ini, optimal: opt, retired,
        capital_cost: cc,
        savings: cc * retired,
        build_year: buildYear(t),
      }]
    })
    return out.sort((a, b) => b.savings - a.savings)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [transformers, assetCosts, costMode])

  const retiredStorage = useMemo<RetiredStorageRow[]>(() => {
    const out = (storageUnits as StorageUnit[]).flatMap<RetiredStorageRow>(s => {
      if (attr<boolean>(s, 'p_nom_extendable') !== true) return []
      const opt = attr<number>(s, 'p_nom_opt')
      const ini = s.p_nom ?? 0
      if (opt == null || !Number.isFinite(opt)) return []
      const delta = opt - ini
      if (delta >= -1e-6) return []
      const retired = -delta
      const hours = s.max_hours ?? 0
      const cc = costPerUnit('storage_units', s.name, s.capital_cost ?? 0)
      return [{
        name: s.name, carrier: s.carrier ?? '',
        initial: ini, optimal: opt, retired,
        capital_cost: cc,
        savings: cc * retired,
        build_year: buildYear(s),
        max_hours: hours,
        retired_energy: retired * hours,
      }]
    })
    return out.sort((a, b) => b.savings - a.savings)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storageUnits, assetCosts, costMode])

  const retiredStores = useMemo<RetiredRowBase[]>(() => {
    const out = (stores as Store[]).flatMap<RetiredRowBase>(s => {
      if (attr<boolean>(s, 'e_nom_extendable') !== true) return []
      const opt = attr<number>(s, 'e_nom_opt')
      const ini = (s as unknown as { e_nom?: number }).e_nom ?? 0
      if (opt == null || !Number.isFinite(opt)) return []
      const delta = opt - ini
      if (delta >= -1e-6) return []
      const retired = -delta
      const cc = costPerUnit('stores', s.name, s.capital_cost ?? 0)
      return [{
        name: s.name, carrier: s.carrier ?? '',
        initial: ini, optimal: opt, retired,
        capital_cost: cc,
        savings: cc * retired,
        build_year: buildYear(s),
      }]
    })
    return out.sort((a, b) => b.savings - a.savings)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stores, assetCosts, costMode])

  const retiredLinks = useMemo<RetiredRowBase[]>(() => {
    const out = (links as LinkT[]).flatMap<RetiredRowBase>(l => {
      if (attr<boolean>(l, 'p_nom_extendable') !== true) return []
      const opt = attr<number>(l, 'p_nom_opt')
      const ini = l.p_nom ?? 0
      if (opt == null || !Number.isFinite(opt)) return []
      const delta = opt - ini
      if (delta >= -1e-6) return []
      const retired = -delta
      const cc = costPerUnit('links', l.name, l.capital_cost ?? 0)
      return [{
        name: l.name, carrier: l.carrier ?? '',
        initial: ini, optimal: opt, retired,
        capital_cost: cc,
        savings: cc * retired,
        build_year: buildYear(l),
      }]
    })
    return out.sort((a, b) => b.savings - a.savings)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [links, assetCosts, costMode])

  // ── Carrier filter ───────────────────────────────────────────────────
  // Union of carriers across all sized + retired rows — drives the filter
  // strip above the per-class tables. Transformers don't carry a carrier
  // attribute in PyPSA so they don't contribute to the union; when a
  // specific carrier is selected the Transformer table auto-hides.
  //
  // The filter only scopes the asset-level breakdowns (CAPEX-by-class
  // chart, per-period charts, per-class tables). System-wide KPIs at the
  // top stay full-system because /api/results/cost_breakdown returns
  // aggregates, not per-carrier slices — re-deriving them client-side
  // here would mismatch PyPSA's own n.statistics() roll-up.
  // Canonicalise each row's carrier through the cross-tab alias map BEFORE
  // collecting into the filter strip — so a user with `BESS` and `Li-Ion`
  // storage sees ONE 'Battery' chip (matching the Dispatch tab's grouping)
  // rather than two distinct chips that don't transfer between tabs.
  // Component class controls which alias scheme applies (storage gets
  // aliased; generators / links / lines stay as raw lowercased values).
  const availableCarriers = useMemo(() => {
    const seen = new Set<string>()
    const push = (cls: ComponentClass, rows: Array<{ carrier?: string }>) => {
      for (const r of rows) seen.add(canonicaliseCarrier(cls, r.carrier))
    }
    push('Generator',   sizedGens)
    push('Line',        sizedLines)
    push('StorageUnit', sizedStorage)
    push('Store',       sizedStores)
    push('Link',        sizedLinks)
    push('Generator',   retiredGens)
    push('Line',        retiredLines)
    push('StorageUnit', retiredStorage)
    push('Store',       retiredStores)
    push('Link',        retiredLinks)
    return [...seen].sort((a, b) => {
      if (a === 'unspecified' && b !== 'unspecified') return 1
      if (b === 'unspecified' && a !== 'unspecified') return -1
      return a.localeCompare(b)
    })
  }, [sizedGens, sizedLines, sizedStorage, sizedStores, sizedLinks,
      retiredGens, retiredLines, retiredStorage, retiredStores, retiredLinks])
  const carrierFilter = useCarrierFilter({
    availableCarriers,
    storageKey: 'results:capacity_expansion:carrier',
  })

  // Apply the filter to each sized + retired array. The carrier on each row
  // is canonicalised through the same alias map used when building
  // `availableCarriers`, so the chip "Battery" matches BESS / Li-Ion /
  // lithium-ion rows. Empty filter (null) → no-op short-circuits.
  const filterByCarrier = <T extends { carrier?: string }>(cls: ComponentClass, rows: T[]): T[] =>
    carrierFilter.isFiltered
      ? rows.filter(r => carrierFilter.matchesSelected(canonicaliseCarrier(cls, r.carrier)))
      : rows
  const visibleGens    = useMemo(() => filterByCarrier('Generator',   sizedGens),    [sizedGens,    carrierFilter])
  const visibleLines   = useMemo(() => filterByCarrier('Line',        sizedLines),   [sizedLines,   carrierFilter])
  const visibleStorage = useMemo(() => filterByCarrier('StorageUnit', sizedStorage), [sizedStorage, carrierFilter])
  const visibleStores  = useMemo(() => filterByCarrier('Store',       sizedStores),  [sizedStores,  carrierFilter])
  const visibleLinks   = useMemo(() => filterByCarrier('Link',        sizedLinks),   [sizedLinks,   carrierFilter])
  // Transformers carry no carrier → only visible when filter is "All".
  // Wrapped in useMemo so the `[]` / `sizedTransformers` swap returns a
  // stable identity per filter state — downstream `capexByClass`,
  // `perPeriod`, `totalRetirementSavings` lists this in their deps; without
  // memo a fresh array literal every render invalidates them constantly.
  const visibleTransformers = useMemo(
    () => carrierFilter.isFiltered ? [] : sizedTransformers,
    [carrierFilter.isFiltered, sizedTransformers],
  )
  const visibleRetiredGens    = useMemo(() => filterByCarrier('Generator',   retiredGens),    [retiredGens,    carrierFilter])
  const visibleRetiredLines   = useMemo(() => filterByCarrier('Line',        retiredLines),   [retiredLines,   carrierFilter])
  const visibleRetiredStorage = useMemo(() => filterByCarrier('StorageUnit', retiredStorage), [retiredStorage, carrierFilter])
  const visibleRetiredStores  = useMemo(() => filterByCarrier('Store',       retiredStores),  [retiredStores,  carrierFilter])
  const visibleRetiredLinks   = useMemo(() => filterByCarrier('Link',        retiredLinks),   [retiredLinks,   carrierFilter])
  const visibleRetiredTransformers = useMemo(
    () => carrierFilter.isFiltered ? [] : retiredTransformers,
    [carrierFilter.isFiltered, retiredTransformers],
  )

  // Total sized assets (for the summary message at the bottom). Reflects
  // the active filter — "12 assets expanded" under an H2 filter should
  // mean 12 H2 assets, not 12 total.
  const totalSized =
    visibleGens.length + visibleLines.length + visibleTransformers.length +
    visibleStorage.length + visibleStores.length + visibleLinks.length
  const totalRetired =
    visibleRetiredGens.length + visibleRetiredLines.length + visibleRetiredTransformers.length +
    visibleRetiredStorage.length + visibleRetiredStores.length + visibleRetiredLinks.length

  const totalRetirementSavings = useMemo(() => {
    const sum = (rs: { savings: number }[]) => rs.reduce((a, r) => a + r.savings, 0)
    return sum(visibleRetiredGens) + sum(visibleRetiredLines) + sum(visibleRetiredTransformers)
         + sum(visibleRetiredStorage) + sum(visibleRetiredStores) + sum(visibleRetiredLinks)
  }, [visibleRetiredGens, visibleRetiredLines, visibleRetiredTransformers,
      visibleRetiredStorage, visibleRetiredStores, visibleRetiredLinks])

  // Bar chart data: INCREMENTAL expansion CAPEX per class, derived from the
  // same sized*-row arrays the Investments tables render. This guarantees the
  // chart and the table can never disagree.
  //
  // Why not pull this from /cost_breakdown's `b.capex` (PyPSA n.statistics
  // 'Capital Expenditure') like before? Because that column is the annualised
  // cost of ALL installed capacity (p_nom × capital_cost for non-extendable,
  // p_nom_opt × capital_cost for extendable) — it includes assets that were
  // never expanded. Users (correctly) read the chart as "capacity expansion"
  // and expect it to match the per-asset table. The mismatch was the bug.
  //
  // The top-row CAPEX KPI keeps the n.statistics() figure (annualised
  // installed-capacity cost) — labelled accordingly via its hint — because
  // that's the right input to system-cost accounting.
  const capexByClass = useMemo(() => {
    const sum = (rs: { capex: number }[]) => rs.reduce((a, r) => a + r.capex, 0)
    return [
      { name: 'Generator',    capex: sum(visibleGens) },
      { name: 'Line',         capex: sum(visibleLines) },
      { name: 'Transformer',  capex: sum(visibleTransformers) },
      { name: 'StorageUnit',  capex: sum(visibleStorage) },
      { name: 'Store',        capex: sum(visibleStores) },
      { name: 'Link',         capex: sum(visibleLinks) },
    ]
      .filter(b => b.capex > 0)
      .sort((a, b) => b.capex - a.capex)
  }, [visibleGens, visibleLines, visibleTransformers, visibleStorage, visibleStores, visibleLinks])

  // System-cost waterfall: how the annual system cost builds up from existing-
  // capacity CAPEX → new-build CAPEX → OPEX, ending at the optimised Total. The
  // step bars sum EXACTLY to Total (Total = capex + opex; existing = capex −
  // capex_expansion). Annual terms only — OPEX has no lifetime variant, so a
  // lifetime waterfall would mix construction with operating cost.
  const costWaterfall = useMemo(() => {
    type Row = { name: string; base: number; value: number; abs: number; color: string }
    if (!cost) return [] as Row[]
    const capex = cost.capex ?? 0
    const expansion = cost.capex_expansion ?? 0
    const existing = Math.max(capex - expansion, 0)
    const opex = cost.opex ?? 0
    const total = cost.total ?? (capex + opex)
    const steps = [
      { name: 'Existing CAPEX', value: existing, color: '#0369a1' },
      { name: 'New-build CAPEX', value: expansion, color: '#16a34a' },
      { name: 'OPEX', value: opex, color: '#d97706' },
    ]
    let cum = 0
    const rows: Row[] = steps
      .filter(s => Math.abs(s.value) > 1e-6)
      .map(s => { const base = cum; cum += s.value; return { name: s.name, base, value: s.value, abs: s.value, color: s.color } })
    rows.push({ name: 'Total', base: 0, value: total, abs: total, color: '#334155' })
    return rows
  }, [cost])

  const totalExpansionCapex = useMemo(
    () => capexByClass.reduce((a, b) => a + b.capex, 0),
    [capexByClass],
  )

  // ── Per-period aggregation (multi-period capacity-expansion runs) ───────
  // Groups every sized asset by its build_year — the investment period the
  // optimiser placed it in. Powers the "Capacity expansion by period" charts.
  // Only build years that are real period labels (finite, > 0) count: a
  // single-period run leaves build_year at 0 / '' for everything, so
  // perPeriod comes back empty and the whole section auto-hides.
  const perPeriod = useMemo(() => {
    const classed: Array<{ cls: string; by: number | ''; delta: number; capex: number }> = [
      ...visibleGens.map(r => ({ cls: 'Generator', by: r.build_year, delta: r.delta, capex: r.capex })),
      ...visibleLines.map(r => ({ cls: 'Line', by: r.build_year, delta: r.delta, capex: r.capex })),
      ...visibleTransformers.map(r => ({ cls: 'Transformer', by: r.build_year, delta: r.delta, capex: r.capex })),
      ...visibleStorage.map(r => ({ cls: 'StorageUnit', by: r.build_year, delta: r.delta, capex: r.capex })),
      ...visibleStores.map(r => ({ cls: 'Store', by: r.build_year, delta: r.delta, capex: r.capex })),
      ...visibleLinks.map(r => ({ cls: 'Link', by: r.build_year, delta: r.delta, capex: r.capex })),
    ]
    const byYear = new Map<number, Record<string, number>>()
    for (const r of classed) {
      const y = typeof r.by === 'number' && Number.isFinite(r.by) && r.by > 0 ? r.by : null
      if (y == null) continue
      let e = byYear.get(y)
      if (!e) { e = { period: y, capacityAdded: 0, capex: 0 }; byYear.set(y, e) }
      e.capacityAdded += r.delta
      e.capex += r.capex
      e[r.cls] = (e[r.cls] ?? 0) + r.capex
    }
    return [...byYear.values()].sort((a, b) => a.period - b.period)
  }, [visibleGens, visibleLines, visibleTransformers, visibleStorage, visibleStores, visibleLinks])

  // Component classes that actually carry per-period CAPEX — drives which
  // stacked <Bar> series the breakdown chart renders.
  const perPeriodClasses = useMemo(
    () => (['Generator', 'Line', 'Transformer', 'StorageUnit', 'Store', 'Link'] as const)
      .filter(c => perPeriod.some(p => (p[c] ?? 0) > 0)),
    [perPeriod],
  )

  const exportCostCSV = () => {
    if (!cost) return
    downloadCSV('results_cost_breakdown.csv',
      ['component', 'capex_EUR', 'opex_EUR', 'total_EUR'],
      cost.by_component.map(b => [b.component, b.capex, b.opex, b.total]),
    )
    toast.success('Exported')
  }

  if (isLoading) return <div className="p-6 text-muted text-xs">Loading…</div>
  if (!cost) return (
    <div className="p-6 text-center text-xs text-muted">
      No solved network. Run an LOPF (header → <span className="font-medium text-text">Run LOPF</span>)
      to populate capacity-expansion results.
    </div>
  )

  // Respect the shared period selector so the cost KPIs line up with the
  // Dispatch tab. With a period picked we show THAT period's totals
  // (annualised, single-period); aggregated we show the years-weighted
  // horizon SUM (`cost.opex` / `cost.capex`) — same contract as Dispatch's
  // `periodEntry?.opex ?? cost.opex`.
  //
  // Period-awareness ONLY applies in annualised mode: `cost.by_period` carries
  // annualised per-period capex/opex, but the lifetime (PV) CAPEX is a single
  // horizon-wide present value with no per-period breakdown. Mixing a horizon
  // PV CAPEX with a single-period OPEX under a "period N" header would be a
  // mixed-scope number, so in lifetime mode the whole strip stays horizon-wide.
  const periodEntry = (costMode === 'annual' && filter.selectedPeriod != null && cost.by_period)
    ? cost.by_period.find(p => p.period === filter.selectedPeriod)
    : null
  const displayOpex = periodEntry?.opex ?? cost.opex
  const displayCapex = costMode === 'lifetime'
    ? (cost.capex_lifetime ?? 0)
    : (periodEntry?.capex ?? cost.capex)
  const scopeLabel = periodEntry ? `period ${filter.selectedPeriod}` : 'horizon (all periods)'

  return (
    <div className="flex flex-col gap-5 p-5 overflow-y-auto h-full [&>*]:shrink-0">

      {/* ── Cost KPIs ──────────────────────────────────────────── */}
      <section>
        <div className="flex items-center justify-between mb-2.5">
          <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">
            Total system cost
            <span className="ml-2 text-[10px] font-normal text-muted">
              {scopeLabel}
            </span>
          </h3>
          <div className="flex items-center gap-2">
            {/* Annualised vs. total-investment toggle. CAPEX-side KPIs and
                the chart below switch between the annual annuity and the
                present value of the upfront overnight investment (future
                build years are discounted back to year-0 of the model). */}
            <Seg
              value={costMode}
              onChange={setCostMode}
              options={[
                { value: 'annual', label: 'Annualised' },
                { value: 'lifetime', label: 'Total investment (PV)' },
              ]}
            />
            <button
              onClick={exportCostCSV}
              className="flex items-center gap-1 text-[11px] text-muted hover:text-accent"
            ><Download size={11} /> CSV</button>
          </div>
        </div>
        <div className="grid grid-cols-4 gap-3">
          {/* New-investment headline — the "how much money is this project asking
              for?" number. Sits in the accent slot so it's the first thing the
              user sees. Mode-aware (annualised vs PV). */}
          <KPI
            accent
            label={costMode === 'lifetime' ? 'Total new investment (PV)' : 'Total new investment (annualised)'}
            // `||` not `??`: PyPSA's n.statistics.expanded_capex() returns 0
            // (not null) for multi-period runs, so `??` would keep the 0.
            // Fall back to the frontend-computed per-asset sum, which is
            // mode-aware (overnight_cost_pv in lifetime mode, capital_cost in
            // annualised) and derived from the same sized-rows the tables show.
            value={fmtCurrency(costMode === 'lifetime'
              ? cost.capex_expansion_lifetime || totalExpansionCapex
              : cost.capex_expansion || totalExpansionCapex)}
            hint={costMode === 'lifetime'
              ? 'Sum of (Δcapacity × overnight_cost × PV factor) for new capacity built this run. PV factor = (1+r)^-(build_year − reference) per asset. Includes every vintage row for assets with per-period bounds.'
              : 'PyPSA n.statistics.expanded_capex() — capital_cost × Δp_nom for new capacity built this run. Includes every vintage row for assets with per-period bounds.'} />
          <KPI
            label={costMode === 'lifetime' ? 'CAPEX (installed, PV)' : 'CAPEX (installed)'}
            value={fmtCurrency(displayCapex)}
            hint={costMode === 'lifetime'
              ? 'Present value of upfront overnight investment for ALL installed capacity. Future-year builds are discounted at each asset\'s discount_rate from build_year back to the model reference year.'
              : periodEntry
                ? `Annualised cost of ALL installed capacity in period ${filter.selectedPeriod} (existing + new).`
                : 'Annualised cost of ALL installed capacity — existing + new, summed across periods (PyPSA n.statistics()).'} />
          <KPI label={periodEntry ? 'OPEX (period)' : 'OPEX (horizon)'} value={fmtCurrency(displayOpex)}
            hint={periodEntry
              ? `Σ marginal_cost × dispatch in period ${filter.selectedPeriod}. Matches the Dispatch tab for this period.`
              : 'Σ marginal_cost × dispatch, weighted and summed across all investment periods. Matches the Dispatch tab when aggregated.'} />
          <KPI
            label="Total system cost"
            value={fmtCurrency(displayCapex + displayOpex)}
            hint={costMode === 'lifetime'
              ? 'PV of upfront investment + OPEX. Mixed-horizon by design — toggle to Annualised for an apples-to-apples comparison.'
              : periodEntry
                ? `Annualised CAPEX (installed) + OPEX for period ${filter.selectedPeriod}.`
                : 'Annualised CAPEX (installed) + OPEX, summed across periods.'} />
          {/* Curtailment penalty and storage-investment slice — these surface
              two numbers the user always wants to see at a glance: the cost
              the LP is paying for spilled renewables, and how much of the
              new investment is going into storage specifically. */}
          <KPI
            label="Curtailment cost"
            value={cost.curtailment_cost > 0 ? fmtCurrency(cost.curtailment_cost) : '—'}
            hint="Σ curtailment_t × curtailment_cost over renewables that opted in (curtailment_cost > 0). Already weighted by snapshot × period years. Zero unless a renewable was given a curtailment_cost." />
          <KPI
            label={costMode === 'lifetime' ? 'Storage CAPEX (new, PV)' : 'Storage CAPEX (new)'}
            value={fmtCurrency(costMode === 'lifetime'
              ? cost.storage_capex_expansion_lifetime ?? 0
              : cost.storage_capex_expansion ?? 0)}
            hint="CAPEX-expansion across StorageUnit + Store assets only — the storage slice of total new investment. Multi-vintage assets contribute one term per build year." />
        </div>
      </section>

      {/* ── System-cost waterfall ──────────────────────────────── */}
      {costWaterfall.length > 1 && (
        <ChartCard
          title="System-cost waterfall"
          hint="annualised, horizon (all periods) — how the Total system cost builds up. Ignores the period selector."
          right={
            <ChartActions
              chartRef={waterfallChartRef}
              onExportCSV={() => {
                downloadCSV('system_cost_waterfall.csv', ['component', 'EUR_per_year'],
                  costWaterfall.map(d => [d.name, d.abs]))
                toast.success('Exported')
              }}
              filename="system_cost_waterfall" />
          }
          bodyClassName="p-3"
        >
          <p className="text-[11px] text-muted mb-3">
            Existing-capacity CAPEX, new-build CAPEX and OPEX stack up to the optimised
            Total annual system cost.{cost && cost.curtailment_cost > 0 && (
              <> A curtailment penalty of <span className="font-medium text-text">{fmtCurrency(cost.curtailment_cost)}</span>{' '}
              is a separate objective term, not part of Total.</>
            )}
          </p>
          <div ref={waterfallChartRef}>
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={costWaterfall} margin={{ top: 4, right: 16, left: 8, bottom: 0 }}>
                <CartesianGrid {...CHART_GRID} />
                <XAxis dataKey="name" {...CHART_AXIS} />
                <YAxis {...CHART_AXIS} tickFormatter={(v: number) => fmtCurrency(v, 1)}
                  label={yAxisLabel('EUR (annualised)')} />
                <RTooltip
                  cursor={{ fill: 'rgba(0,0,0,0.04)' }}
                  content={(props: { active?: boolean; payload?: Array<{ payload?: { name: string; abs: number } }> }) => {
                    const row = props.active ? props.payload?.[0]?.payload : undefined
                    if (!row) return null
                    return (
                      <div className="rounded border border-border bg-bg px-2 py-1 text-[11px] shadow">
                        <div className="text-text font-medium">{row.name}</div>
                        <div className="text-muted font-mono">{fmtCurrency(row.abs)}</div>
                      </div>
                    )
                  }}
                />
                {/* Transparent offset bar lifts each step to its cumulative base. */}
                <Bar dataKey="base" stackId="wf" fill="transparent" isAnimationActive={false} />
                <Bar dataKey="value" stackId="wf" radius={[4, 4, 0, 0]} isAnimationActive={false}>
                  {costWaterfall.map((d, i) => <Cell key={i} fill={d.color} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>
      )}

      {/* ── Expansion CAPEX by component class ─────────────────── */}
      <ChartCard
        title="Expansion CAPEX by component class"
        hint={costMode === 'lifetime' ? 'PV of upfront investment' : 'annualised'}
        right={capexByClass.length > 0 && (
          <ChartActions
            chartRef={capexChartRef}
            onExportCSV={() => {
              // Suffix the filename with the active carrier so a user who
              // compares carriers doesn't end up overwriting the same
              // CSV. No suffix on "All" so the legacy filename survives.
              const carrierSuf = carrierFilter.isFiltered ? `_${carrierFilter.selectedCarrier}` : ''
              downloadCSV(`expansion_capex_${costMode}${carrierSuf}.csv`,
                ['component', 'capex_EUR'],
                capexByClass.map(d => [d.name, d.capex]))
              toast.success('Exported')
            }}
            filename={`expansion_capex_${costMode}${carrierFilter.isFiltered ? `_${carrierFilter.selectedCarrier}` : ''}`} />
        )}
        bodyClassName="p-3"
      >
        <p className="text-[11px] text-muted mb-3">
          Incremental investment cost of the <span className="font-medium text-text">new</span> capacity built by
          the optimiser{costMode === 'lifetime'
            ? ' (Δ × overnight_cost discounted to year 0 via (1+r)^-(build_year − ref_year))'
            : ' (Δ × annualised capital_cost)'}. Differs from the top-row CAPEX KPI, which covers <em>all</em>
          installed capacity including assets that already existed.
        </p>
        {capexByClass.length === 0 ? (
          <div className="py-7 text-center text-[11.5px] text-muted">
            No capacity-expansion costs (no extendable assets, or none were sized up).
          </div>
        ) : (
          <div ref={capexChartRef}>
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={capexByClass} margin={{ top: 4, right: 16, left: 8, bottom: 0 }}>
                <CartesianGrid {...CHART_GRID} />
                <XAxis dataKey="name" {...CHART_AXIS} />
                <YAxis {...CHART_AXIS} tickFormatter={(v: number) => fmtCurrency(v, 1)}
                  label={yAxisLabel(costMode === 'lifetime' ? 'EUR (PV)' : 'EUR (annualised)')} />
                <RTooltip {...CHART_TOOLTIP} formatter={(v: number) => fmtCurrency(v)} />
                <Bar dataKey="capex" radius={[4, 4, 0, 0]}>
                  {capexByClass.map((d, i) => (
                    <Cell key={i} fill={COMPONENT_COLOR[d.name] ?? '#6b7280'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </ChartCard>

      {/* ── Capacity expansion by period (multi-period runs only) ── */}
      {/* Auto-hides on single-period runs — perPeriod is empty when no asset
          carries a real build_year. Two charts: total capacity + CAPEX per
          period, then the same CAPEX broken down by component class. */}
      {perPeriod.length > 0 && (
        <>
          <ChartCard
            title="Capacity expansion by period"
            hint="grouped by build year"
            right={
              <ChartActions
                chartRef={perPeriodCapRef}
                onExportCSV={() => {
                  const carrierSuf = carrierFilter.isFiltered ? `_${carrierFilter.selectedCarrier}` : ''
                  downloadCSV(`capacity_expansion_by_period${carrierSuf}.csv`,
                    ['period', 'capacity_added', 'capex_EUR'],
                    perPeriod.map(p => [p.period, p.capacityAdded, p.capex]))
                  toast.success('Exported')
                }}
                filename={`capacity_expansion_by_period${carrierFilter.isFiltered ? `_${carrierFilter.selectedCarrier}` : ''}`} />
            }
            bodyClassName="p-3"
          >
            <p className="text-[11px] text-muted mb-3">
              New capacity and incremental investment the optimiser placed in
              each investment period (asset <code>build_year</code>). Capacity
              is a nameplate sum across component classes (MW · MVA · MWh);
              CAPEX is {costMode === 'lifetime' ? 'present-value upfront investment' : 'annualised'}.
            </p>
            <div ref={perPeriodCapRef}>
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={perPeriod} margin={{ top: 4, right: 16, left: 8, bottom: 0 }}>
                  <CartesianGrid {...CHART_GRID} />
                  <XAxis dataKey="period" {...CHART_AXIS} />
                  <YAxis yAxisId="cap" {...CHART_AXIS}
                    tickFormatter={(v: number) => fmtPower(v, 0)}
                    label={yAxisLabel('Capacity added')} />
                  <YAxis yAxisId="capex" orientation="right" {...CHART_AXIS}
                    tickFormatter={(v: number) => fmtCurrency(v, 1)} />
                  <RTooltip {...CHART_TOOLTIP}
                    formatter={(v: number, name: string) =>
                      name === 'Total CAPEX' ? fmtCurrency(v) : fmtPower(v)} />
                  <Legend {...CHART_LEGEND} />
                  <Bar yAxisId="cap" dataKey="capacityAdded" name="Capacity added"
                    fill="#16a34a" radius={[4, 4, 0, 0]} />
                  <Bar yAxisId="capex" dataKey="capex" name="Total CAPEX"
                    fill="#dc2626" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </ChartCard>

          <ChartCard
            title="Investment by asset class, per period"
            hint={costMode === 'lifetime' ? 'PV of upfront investment' : 'annualised'}
            right={
              <ChartActions
                chartRef={perPeriodClassRef}
                onExportCSV={() => {
                  const carrierSuf = carrierFilter.isFiltered ? `_${carrierFilter.selectedCarrier}` : ''
                  downloadCSV(`investment_by_class_per_period${carrierSuf}.csv`,
                    ['period', ...perPeriodClasses, 'total_EUR'],
                    perPeriod.map(p => [
                      p.period,
                      ...perPeriodClasses.map(c => p[c] ?? 0),
                      p.capex,
                    ]))
                  toast.success('Exported')
                }}
                filename={`investment_by_class_per_period${carrierFilter.isFiltered ? `_${carrierFilter.selectedCarrier}` : ''}`} />
            }
            bodyClassName="p-3"
          >
            <p className="text-[11px] text-muted mb-3">
              The same per-period investment, broken down by component class —
              shows which technologies the optimiser invested in, and when.
            </p>
            <div ref={perPeriodClassRef}>
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={perPeriod} margin={{ top: 4, right: 16, left: 8, bottom: 0 }}>
                  <CartesianGrid {...CHART_GRID} />
                  <XAxis dataKey="period" {...CHART_AXIS} />
                  <YAxis {...CHART_AXIS} tickFormatter={(v: number) => fmtCurrency(v, 1)}
                    label={yAxisLabel(costMode === 'lifetime' ? 'EUR (PV)' : 'EUR (annualised)')} />
                  <RTooltip {...CHART_TOOLTIP} formatter={(v: number) => fmtCurrency(v)} />
                  <Legend {...CHART_LEGEND} />
                  {perPeriodClasses.map(c => (
                    <Bar key={c} dataKey={c} name={c} stackId="capex"
                      fill={COMPONENT_COLOR[c] ?? '#6b7280'} />
                  ))}
                </BarChart>
              </ResponsiveContainer>
            </div>
          </ChartCard>
        </>
      )}

      {/* ── Per-class disaggregated tables ─────────────────────── */}
      <section className="flex flex-col gap-4">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-baseline gap-2">
            <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">Investments by asset</h3>
            <span className="text-[11px] text-muted">
              {totalSized} asset{totalSized === 1 ? '' : 's'} expanded
              {carrierFilter.isFiltered && ` · filtered to ${carrierFilter.selectedCarrier}`}
            </span>
          </div>
          {/* Carrier filter strip — scopes the per-class tables, CAPEX-by-class
              chart, and per-period charts. Top-row KPIs stay full-system
              because n.statistics()'s aggregates can't be sliced client-side
              without losing per-carrier accuracy. */}
          <CarrierFilter {...bindCarrierFilter(carrierFilter, availableCarriers)} label="Carrier" />
        </div>

        {totalSized === 0 ? (
          <div className="border border-border rounded p-4 text-center text-xs text-muted">
            {carrierFilter.isFiltered
              ? `No expanded assets match carrier "${carrierFilter.selectedCarrier}". Switch the filter to "All" to see every expanded asset.`
              : 'No assets had capacity expansion (every extendable asset stayed at its initial size).'}
          </div>
        ) : (
          <>
            {visibleGens.length > 0 && (
              <GenerationTable rows={visibleGens} />
            )}
            {visibleLines.length > 0 && (
              <LinesTable rows={visibleLines} />
            )}
            {visibleTransformers.length > 0 && (
              <TransformersTable rows={visibleTransformers} />
            )}
            {visibleStorage.length > 0 && (
              <StorageTable rows={visibleStorage} />
            )}
            {visibleStores.length > 0 && (
              <StoresTable rows={visibleStores} />
            )}
            {visibleLinks.length > 0 && (
              <LinksTable rows={visibleLinks} />
            )}
          </>
        )}
      </section>

      {/* ── Retired assets ─────────────────────────────────────── */}
      {/* Only renders when at least one asset shrank below its initial size.
          For typical electricity runs this is empty (most assets are either
          non-extendable or have p_nom_min ≥ p_nom, making decommissioning
          infeasible). Shown right after Investments so the user has both
          directions of capacity change in one place. */}
      {totalRetired > 0 && (
        <section className="flex flex-col gap-4">
          <div className="flex items-center justify-between">
            <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">Retired assets</h3>
            <span className="text-[11px] text-muted">
              {totalRetired} asset{totalRetired === 1 ? '' : 's'} retired
              {' · saves '}{fmtCurrency(totalRetirementSavings)}
            </span>
          </div>
          {visibleRetiredGens.length > 0       && <RetirementGenerationTable rows={visibleRetiredGens} />}
          {visibleRetiredLines.length > 0      && <RetirementLinesTable rows={visibleRetiredLines} />}
          {visibleRetiredTransformers.length > 0 && <RetirementTransformersTable rows={visibleRetiredTransformers} />}
          {visibleRetiredStorage.length > 0    && <RetirementStorageTable rows={visibleRetiredStorage} />}
          {visibleRetiredStores.length > 0     && <RetirementStoresTable rows={visibleRetiredStores} />}
          {visibleRetiredLinks.length > 0      && <RetirementLinksTable rows={visibleRetiredLinks} />}
        </section>
      )}
    </div>
  )
}

// ── Subsection table primitives ────────────────────────────────────────────
// One wrapper takes the title + count + columns + CSV export, every per-class
// table just declares what its row looks like. Keeps the per-class tables
// consistent without making every body cell pluggable through props.

function SubTable({
  title, color, count, onExport, children, searchSlot,
}: {
  title: string; color: string; count: number; onExport: () => void
  children: React.ReactNode
  // Optional filter/search input rendered between the count and the CSV
  // button. The wrapping table opts in by passing a <TableSearchBox/> from
  // its own `useFilterableTable` hook — keeps the SubTable shell oblivious
  // to filter state so tables that don't need filtering stay unchanged.
  searchSlot?: React.ReactNode
}) {
  return (
    <div className="border border-border rounded overflow-hidden">
      <div
        className="flex items-center justify-between px-3 py-1.5 border-b border-border gap-2 flex-wrap"
        style={{ background: `${color}10` }}
      >
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded"
            style={{ background: `${color}25`, color }}>
            {title}
          </span>
          <span className="text-[11px] text-muted">{count} asset{count === 1 ? '' : 's'} expanded</span>
        </div>
        <div className="flex items-center gap-2 ml-auto">
          {searchSlot}
          <button
            onClick={onExport}
            className="flex items-center gap-1 text-[11px] text-muted hover:text-accent"
          ><Download size={11} /> CSV</button>
        </div>
      </div>
      <div className="overflow-auto max-h-72">
        <table className="w-full text-xs border-collapse" style={{ minWidth: 640 }}>
          {children}
        </table>
      </div>
    </div>
  )
}

const Th = ({ children, align = 'left' }: { children: React.ReactNode; align?: 'left' | 'right' }) => (
  <th className={`px-2 py-1.5 text-[10px] font-semibold text-muted uppercase tracking-wide whitespace-nowrap ${align === 'right' ? 'text-right' : 'text-left'}`}>
    {children}
  </th>
)
const Td = ({ children, align = 'left', mono = false, bold = false, color }: {
  children: React.ReactNode; align?: 'left' | 'right'; mono?: boolean; bold?: boolean; color?: string
}) => (
  <td
    className={`px-2 py-1 text-[11px] ${align === 'right' ? 'text-right' : ''} ${mono ? 'font-mono' : ''} ${bold ? 'font-semibold' : ''}`}
    style={color ? { color } : undefined}
  >{children}</td>
)
const RowBg = ({ i, children }: { i: number; children: React.ReactNode }) => (
  <tr className={i % 2 === 0 ? 'bg-bg' : 'bg-panel'}>{children}</tr>
)

// ── Generation (Generators) ─────────────────────────────────────────────────
// Combined conventional + renewable in one table; a `Type` column tells them
// apart so the user can sort/scan by category without splitting into two
// sub-tables (which would inflate vertical space for small networks).
function GenerationTable({ rows }: { rows: SizedRowBase[] }) {
  type GenKey = 'name' | 'type' | 'carrier' | 'initial' | 'delta' | 'optimal' | 'build_year' | 'capital_cost' | 'capex'
  const ft = useFilterableTable<SizedRowBase, GenKey>({
    rows,
    initialSortKey: null,
    getSearchText: r => [
      r.name, r.carrier ?? '',
      isRenewableCarrier(r.carrier ?? '') ? 'renewable' : 'conventional',
    ].join(' ').toLowerCase(),
    getValue: (r, k) => {
      if (k === 'type') return isRenewableCarrier(r.carrier ?? '') ? 'renewable' : 'conventional'
      if (k === 'name' || k === 'carrier') return r[k] ?? ''
      return r[k] as number
    },
  })
  const exportCSV = () => downloadCSV(
    'results_generation_expansion.csv',
    ['name', 'type', 'carrier', 'initial_MW', 'optimal_MW', 'delta_MW',
     'build_year', 'annualised_capital_cost_EUR_per_MW', 'capex_EUR'],
    ft.rows.map(r => [
      r.name,
      isRenewableCarrier(r.carrier ?? '') ? 'Renewable' : 'Conventional',
      r.carrier ?? '',
      r.initial, r.optimal, r.delta,
      r.build_year, r.capital_cost, r.capex,
    ]),
  )
  return (
    <SubTable
      title="Generation" color={COMPONENT_COLOR.Generator} count={rows.length}
      onExport={exportCSV}
      searchSlot={<TableSearchBox value={ft.search} onChange={ft.setSearch} placeholder="Filter by name / carrier…" />}
    >
      <thead className="sticky top-0 bg-bg-2 z-10">
        <tr className="border-b border-border text-[10px] font-semibold text-muted uppercase tracking-wide">
          <SortHeader columnKey="name"         label="Name"        sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('name')}         className="px-2" />
          <SortHeader columnKey="type"         label="Type"        sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('type')}         className="px-2" />
          <SortHeader columnKey="carrier"      label="Carrier"     sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('carrier')}      className="px-2" />
          <SortHeader columnKey="initial"      label="Initial"     sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('initial')}      className="px-2" align="right" />
          <SortHeader columnKey="delta"        label="Built"       sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('delta')}        className="px-2" align="right" />
          <SortHeader columnKey="optimal"      label="Total"       sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('optimal')}      className="px-2" align="right" />
          <SortHeader columnKey="build_year"   label="Build year"  sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('build_year')}   className="px-2" align="right" />
          <SortHeader columnKey="capital_cost" label="CAPEX €/MW"  sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('capital_cost')} className="px-2" align="right" />
          <SortHeader columnKey="capex"        label="CAPEX"       sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('capex')}        className="px-2" align="right" />
        </tr>
      </thead>
      <tbody>
        {ft.rows.map((r, i) => {
          const isRen = isRenewableCarrier(r.carrier ?? '')
          return (
            <RowBg i={i} key={`${r.name}@${r.build_year}`}>
              <Td mono>{r.name}</Td>
              <Td>
                <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                  isRen ? 'bg-success/15 text-success' : 'bg-warn/15 text-warn'
                }`}>{isRen ? 'Renewable' : 'Conventional'}</span>
              </Td>
              <Td><span className="text-muted">{r.carrier}</span></Td>
              <Td align="right" mono>{fmtPower(r.initial, 1)}</Td>
              <Td align="right" mono color="#16a34a">+{fmtPower(r.delta, 1)}</Td>
              <Td align="right" mono>{fmtPower(r.optimal, 1)}</Td>
              <Td align="right" mono>{r.build_year || '—'}</Td>
              <Td align="right" mono>{fmtCurrency(r.capital_cost, 1)}</Td>
              <Td align="right" mono bold>{fmtCurrency(r.capex, 1)}</Td>
            </RowBg>
          )
        })}
      </tbody>
    </SubTable>
  )
}

// ── Lines ───────────────────────────────────────────────────────────────────
// MVA per asset = s_nom. Length stays per-line (km). build_year is uncommon
// on Line in older PyPSA versions; rendered as "—" when missing.
function LinesTable({ rows }: { rows: SizedLineRow[] }) {
  const exportCSV = () => downloadCSV(
    'results_lines_expansion.csv',
    ['name', 'length_km', 'initial_MVA', 'optimal_MVA', 'delta_MVA',
     'build_year', 'annualised_capital_cost_EUR_per_MVA_km', 'capex_EUR'],
    rows.map(r => [r.name, r.length, r.initial, r.optimal, r.delta, r.build_year, r.capital_cost, r.capex]),
  )
  return (
    <SubTable title="Lines" color={COMPONENT_COLOR.Line} count={rows.length} onExport={exportCSV}>
      <thead className="sticky top-0 bg-bg-2 z-10">
        <tr className="border-b border-border">
          <Th>Name</Th>
          <Th align="right">Length</Th>
          <Th align="right">Initial</Th><Th align="right">Built</Th><Th align="right">Total</Th>
          <Th align="right">Build year</Th>
          <Th align="right">CAPEX</Th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <RowBg i={i} key={`${r.name}@${r.build_year}`}>
            <Td mono>{r.name}</Td>
            <Td align="right" mono>{r.length ? `${r.length.toFixed(1)} km` : '—'}</Td>
            <Td align="right" mono>{fmtPower(r.initial, 1).replace('MW', 'MVA')}</Td>
            <Td align="right" mono color="#16a34a">+{fmtPower(r.delta, 1).replace('MW', 'MVA')}</Td>
            <Td align="right" mono>{fmtPower(r.optimal, 1).replace('MW', 'MVA')}</Td>
            <Td align="right" mono>{r.build_year || '—'}</Td>
            <Td align="right" mono bold>{fmtCurrency(r.capex, 1)}</Td>
          </RowBg>
        ))}
      </tbody>
    </SubTable>
  )
}

// ── Transformers ────────────────────────────────────────────────────────────
// PyPSA models transformer rating as s_nom (MVA). We don't render a Carrier
// column because Transformer doesn't carry one in PyPSA's data model.
function TransformersTable({ rows }: { rows: SizedRowBase[] }) {
  const exportCSV = () => downloadCSV(
    'results_transformers_expansion.csv',
    ['name', 'initial_MVA', 'optimal_MVA', 'delta_MVA', 'build_year',
     'annualised_capital_cost_EUR_per_MVA', 'capex_EUR'],
    rows.map(r => [r.name, r.initial, r.optimal, r.delta, r.build_year, r.capital_cost, r.capex]),
  )
  return (
    <SubTable title="Transformers" color={COMPONENT_COLOR.Transformer} count={rows.length} onExport={exportCSV}>
      <thead className="sticky top-0 bg-bg-2 z-10">
        <tr className="border-b border-border">
          <Th>Name</Th>
          <Th align="right">Initial</Th><Th align="right">Built</Th><Th align="right">Total</Th>
          <Th align="right">Build year</Th>
          <Th align="right">CAPEX</Th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <RowBg i={i} key={`${r.name}@${r.build_year}`}>
            <Td mono>{r.name}</Td>
            <Td align="right" mono>{fmtPower(r.initial, 1).replace('MW', 'MVA')}</Td>
            <Td align="right" mono color="#16a34a">+{fmtPower(r.delta, 1).replace('MW', 'MVA')}</Td>
            <Td align="right" mono>{fmtPower(r.optimal, 1).replace('MW', 'MVA')}</Td>
            <Td align="right" mono>{r.build_year || '—'}</Td>
            <Td align="right" mono bold>{fmtCurrency(r.capex, 1)}</Td>
          </RowBg>
        ))}
      </tbody>
    </SubTable>
  )
}

// ── Storage Units ──────────────────────────────────────────────────────────
// StorageUnit's "size" is power (p_nom MW); the energy rating MWh is derived
// as p_nom × max_hours. Both shown so the user can spot if max_hours was set
// inconsistently across the storage fleet.
function StorageTable({ rows }: { rows: SizedStorageRow[] }) {
  type SKey = 'name' | 'carrier' | 'initial' | 'delta' | 'optimal' | 'max_hours' | 'optimal_energy' | 'build_year' | 'capex'
  const ft = useFilterableTable<SizedStorageRow, SKey>({
    rows,
    getSearchText: r => [r.name, r.carrier ?? ''].join(' ').toLowerCase(),
    getValue: (r, k) => (k === 'name' || k === 'carrier') ? (r[k] ?? '') : r[k] as number,
  })
  const exportCSV = () => downloadCSV(
    'results_storage_expansion.csv',
    ['name', 'carrier', 'initial_MW', 'optimal_MW', 'delta_MW',
     'max_hours', 'optimal_MWh', 'build_year',
     'annualised_capital_cost_EUR_per_MW', 'capex_EUR'],
    ft.rows.map(r => [
      r.name, r.carrier ?? '',
      r.initial, r.optimal, r.delta,
      r.max_hours, r.optimal_energy, r.build_year,
      r.capital_cost, r.capex,
    ]),
  )
  return (
    <SubTable
      title="Storage Units" color={COMPONENT_COLOR.StorageUnit} count={rows.length}
      onExport={exportCSV}
      searchSlot={<TableSearchBox value={ft.search} onChange={ft.setSearch} placeholder="Filter…" />}
    >
      <thead className="sticky top-0 bg-bg-2 z-10">
        <tr className="border-b border-border text-[10px] font-semibold text-muted uppercase tracking-wide">
          <SortHeader columnKey="name"           label="Name"       sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('name')}           className="px-2" />
          <SortHeader columnKey="carrier"        label="Carrier"    sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('carrier')}        className="px-2" />
          <SortHeader columnKey="initial"        label="Initial"    sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('initial')}        className="px-2" align="right" />
          <SortHeader columnKey="delta"          label="Built"      sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('delta')}          className="px-2" align="right" />
          <SortHeader columnKey="optimal"        label="Total"      sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('optimal')}        className="px-2" align="right" />
          <SortHeader columnKey="max_hours"      label="Hours"      sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('max_hours')}      className="px-2" align="right" />
          <SortHeader columnKey="optimal_energy" label="Energy"     sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('optimal_energy')} className="px-2" align="right" />
          <SortHeader columnKey="build_year"     label="Build year" sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('build_year')}     className="px-2" align="right" />
          <SortHeader columnKey="capex"          label="CAPEX"      sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('capex')}          className="px-2" align="right" />
        </tr>
      </thead>
      <tbody>
        {ft.rows.map((r, i) => (
          <RowBg i={i} key={`${r.name}@${r.build_year}`}>
            <Td mono>{r.name}</Td>
            <Td><span className="text-muted">{r.carrier ?? ''}</span></Td>
            <Td align="right" mono>{fmtPower(r.initial, 1)}</Td>
            <Td align="right" mono color="#16a34a">+{fmtPower(r.delta, 1)}</Td>
            <Td align="right" mono>{fmtPower(r.optimal, 1)}</Td>
            <Td align="right" mono>{r.max_hours ? r.max_hours.toFixed(1) : '—'}</Td>
            <Td align="right" mono>{fmtEnergy(r.optimal_energy, 1)}</Td>
            <Td align="right" mono>{r.build_year || '—'}</Td>
            <Td align="right" mono bold>{fmtCurrency(r.capex, 1)}</Td>
          </RowBg>
        ))}
      </tbody>
    </SubTable>
  )
}

// ── Stores ─────────────────────────────────────────────────────────────────
// Store's primary capacity is energy (e_nom MWh) — they have no separate
// power rating in PyPSA (that's the connected Link's job).
function StoresTable({ rows }: { rows: SizedRowBase[] }) {
  const exportCSV = () => downloadCSV(
    'results_stores_expansion.csv',
    ['name', 'carrier', 'initial_MWh', 'optimal_MWh', 'delta_MWh',
     'build_year', 'annualised_capital_cost_EUR_per_MWh', 'capex_EUR'],
    rows.map(r => [r.name, r.carrier ?? '', r.initial, r.optimal, r.delta, r.build_year, r.capital_cost, r.capex]),
  )
  return (
    <SubTable title="Stores" color={COMPONENT_COLOR.Store} count={rows.length} onExport={exportCSV}>
      <thead className="sticky top-0 bg-bg-2 z-10">
        <tr className="border-b border-border">
          <Th>Name</Th><Th>Carrier</Th>
          <Th align="right">Initial</Th><Th align="right">Built</Th><Th align="right">Total</Th>
          <Th align="right">Build year</Th>
          <Th align="right">CAPEX</Th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <RowBg i={i} key={`${r.name}@${r.build_year}`}>
            <Td mono>{r.name}</Td>
            <Td><span className="text-muted">{r.carrier ?? ''}</span></Td>
            <Td align="right" mono>{fmtEnergy(r.initial, 1)}</Td>
            <Td align="right" mono color="#16a34a">+{fmtEnergy(r.delta, 1)}</Td>
            <Td align="right" mono>{fmtEnergy(r.optimal, 1)}</Td>
            <Td align="right" mono>{r.build_year || '—'}</Td>
            <Td align="right" mono bold>{fmtCurrency(r.capex, 1)}</Td>
          </RowBg>
        ))}
      </tbody>
    </SubTable>
  )
}

// ── Links (power-flow conversion devices: electrolysers, fuel cells, P2H) ──
function LinksTable({ rows }: { rows: SizedRowBase[] }) {
  type LKey = 'name' | 'carrier' | 'initial' | 'delta' | 'optimal' | 'build_year' | 'capex'
  const ft = useFilterableTable<SizedRowBase, LKey>({
    rows,
    getSearchText: r => [r.name, r.carrier ?? ''].join(' ').toLowerCase(),
    getValue: (r, k) => (k === 'name' || k === 'carrier') ? (r[k] ?? '') : r[k] as number,
  })
  const exportCSV = () => downloadCSV(
    'results_links_expansion.csv',
    ['name', 'carrier', 'initial_MW', 'optimal_MW', 'delta_MW',
     'build_year', 'annualised_capital_cost_EUR_per_MW', 'capex_EUR'],
    ft.rows.map(r => [r.name, r.carrier ?? '', r.initial, r.optimal, r.delta, r.build_year, r.capital_cost, r.capex]),
  )
  return (
    <SubTable
      title="Links" color={COMPONENT_COLOR.Link} count={rows.length}
      onExport={exportCSV}
      searchSlot={<TableSearchBox value={ft.search} onChange={ft.setSearch} placeholder="Filter…" />}
    >
      <thead className="sticky top-0 bg-bg-2 z-10">
        <tr className="border-b border-border text-[10px] font-semibold text-muted uppercase tracking-wide">
          <SortHeader columnKey="name"       label="Name"       sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('name')}       className="px-2" />
          <SortHeader columnKey="carrier"    label="Carrier"    sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('carrier')}    className="px-2" />
          <SortHeader columnKey="initial"    label="Initial"    sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('initial')}    className="px-2" align="right" />
          <SortHeader columnKey="delta"      label="Built"      sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('delta')}      className="px-2" align="right" />
          <SortHeader columnKey="optimal"    label="Total"      sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('optimal')}    className="px-2" align="right" />
          <SortHeader columnKey="build_year" label="Build year" sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('build_year')} className="px-2" align="right" />
          <SortHeader columnKey="capex"      label="CAPEX"      sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('capex')}      className="px-2" align="right" />
        </tr>
      </thead>
      <tbody>
        {ft.rows.map((r, i) => (
          <RowBg i={i} key={`${r.name}@${r.build_year}`}>
            <Td mono>{r.name}</Td>
            <Td><span className="text-muted">{r.carrier ?? ''}</span></Td>
            <Td align="right" mono>{fmtPower(r.initial, 1)}</Td>
            <Td align="right" mono color="#16a34a">+{fmtPower(r.delta, 1)}</Td>
            <Td align="right" mono>{fmtPower(r.optimal, 1)}</Td>
            <Td align="right" mono>{r.build_year || '—'}</Td>
            <Td align="right" mono bold>{fmtCurrency(r.capex, 1)}</Td>
          </RowBg>
        ))}
      </tbody>
    </SubTable>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Retirement tables — same structural template as the Investments tables, with
// (retired, savings) replacing (delta, capex). Coloured danger-red on the
// minus column so retirements are visually distinct from green expansion rows.
// Build year column shows when the asset was originally placed in service —
// useful for "is the optimiser ripping out my newest stuff or my oldest?"
// inspection.
// ─────────────────────────────────────────────────────────────────────────────

const RETIRED_COLOR = '#dc2626'  // danger red — applies to all retirement table headers

function RetirementGenerationTable({ rows }: { rows: RetiredRowBase[] }) {
  const exportCSV = () => downloadCSV(
    'results_generation_retired.csv',
    ['name', 'type', 'carrier', 'initial_MW', 'optimal_MW', 'retired_MW',
     'build_year', 'annualised_capital_cost_EUR_per_MW', 'savings_EUR'],
    rows.map(r => [
      r.name,
      isRenewableCarrier(r.carrier ?? '') ? 'Renewable' : 'Conventional',
      r.carrier ?? '',
      r.initial, r.optimal, r.retired,
      r.build_year, r.capital_cost, r.savings,
    ]),
  )
  return (
    <SubTable title="Generation" color={RETIRED_COLOR} count={rows.length} onExport={exportCSV}>
      <thead className="sticky top-0 bg-bg-2 z-10">
        <tr className="border-b border-border">
          <Th>Name</Th><Th>Type</Th><Th>Carrier</Th>
          <Th align="right">Initial</Th><Th align="right">Retired</Th><Th align="right">Remaining</Th>
          <Th align="right">Build year</Th>
          <Th align="right">CAPEX €/MW</Th><Th align="right">Savings</Th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => {
          const isRen = isRenewableCarrier(r.carrier ?? '')
          return (
            <RowBg i={i} key={`${r.name}@${r.build_year}`}>
              <Td mono>{r.name}</Td>
              <Td>
                <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                  isRen ? 'bg-success/15 text-success' : 'bg-warn/15 text-warn'
                }`}>{isRen ? 'Renewable' : 'Conventional'}</span>
              </Td>
              <Td><span className="text-muted">{r.carrier}</span></Td>
              <Td align="right" mono>{fmtPower(r.initial, 1)}</Td>
              <Td align="right" mono color={RETIRED_COLOR}>−{fmtPower(r.retired, 1)}</Td>
              <Td align="right" mono>{fmtPower(r.optimal, 1)}</Td>
              <Td align="right" mono>{r.build_year || '—'}</Td>
              <Td align="right" mono>{fmtCurrency(r.capital_cost, 1)}</Td>
              <Td align="right" mono bold>{fmtCurrency(r.savings, 1)}</Td>
            </RowBg>
          )
        })}
      </tbody>
    </SubTable>
  )
}

function RetirementLinesTable({ rows }: { rows: RetiredLineRow[] }) {
  const exportCSV = () => downloadCSV(
    'results_lines_retired.csv',
    ['name', 'length_km', 'initial_MVA', 'optimal_MVA', 'retired_MVA',
     'build_year', 'annualised_capital_cost_EUR_per_MVA_km', 'savings_EUR'],
    rows.map(r => [r.name, r.length, r.initial, r.optimal, r.retired, r.build_year, r.capital_cost, r.savings]),
  )
  return (
    <SubTable title="Lines" color={RETIRED_COLOR} count={rows.length} onExport={exportCSV}>
      <thead className="sticky top-0 bg-bg-2 z-10">
        <tr className="border-b border-border">
          <Th>Name</Th>
          <Th align="right">Length</Th>
          <Th align="right">Initial</Th><Th align="right">Retired</Th><Th align="right">Remaining</Th>
          <Th align="right">Build year</Th>
          <Th align="right">Savings</Th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <RowBg i={i} key={`${r.name}@${r.build_year}`}>
            <Td mono>{r.name}</Td>
            <Td align="right" mono>{r.length ? `${r.length.toFixed(1)} km` : '—'}</Td>
            <Td align="right" mono>{fmtPower(r.initial, 1).replace('MW', 'MVA')}</Td>
            <Td align="right" mono color={RETIRED_COLOR}>−{fmtPower(r.retired, 1).replace('MW', 'MVA')}</Td>
            <Td align="right" mono>{fmtPower(r.optimal, 1).replace('MW', 'MVA')}</Td>
            <Td align="right" mono>{r.build_year || '—'}</Td>
            <Td align="right" mono bold>{fmtCurrency(r.savings, 1)}</Td>
          </RowBg>
        ))}
      </tbody>
    </SubTable>
  )
}

function RetirementTransformersTable({ rows }: { rows: RetiredRowBase[] }) {
  const exportCSV = () => downloadCSV(
    'results_transformers_retired.csv',
    ['name', 'initial_MVA', 'optimal_MVA', 'retired_MVA', 'build_year',
     'annualised_capital_cost_EUR_per_MVA', 'savings_EUR'],
    rows.map(r => [r.name, r.initial, r.optimal, r.retired, r.build_year, r.capital_cost, r.savings]),
  )
  return (
    <SubTable title="Transformers" color={RETIRED_COLOR} count={rows.length} onExport={exportCSV}>
      <thead className="sticky top-0 bg-bg-2 z-10">
        <tr className="border-b border-border">
          <Th>Name</Th>
          <Th align="right">Initial</Th><Th align="right">Retired</Th><Th align="right">Remaining</Th>
          <Th align="right">Build year</Th>
          <Th align="right">Savings</Th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <RowBg i={i} key={`${r.name}@${r.build_year}`}>
            <Td mono>{r.name}</Td>
            <Td align="right" mono>{fmtPower(r.initial, 1).replace('MW', 'MVA')}</Td>
            <Td align="right" mono color={RETIRED_COLOR}>−{fmtPower(r.retired, 1).replace('MW', 'MVA')}</Td>
            <Td align="right" mono>{fmtPower(r.optimal, 1).replace('MW', 'MVA')}</Td>
            <Td align="right" mono>{r.build_year || '—'}</Td>
            <Td align="right" mono bold>{fmtCurrency(r.savings, 1)}</Td>
          </RowBg>
        ))}
      </tbody>
    </SubTable>
  )
}

function RetirementStorageTable({ rows }: { rows: RetiredStorageRow[] }) {
  const exportCSV = () => downloadCSV(
    'results_storage_retired.csv',
    ['name', 'carrier', 'initial_MW', 'optimal_MW', 'retired_MW',
     'max_hours', 'retired_MWh', 'build_year',
     'annualised_capital_cost_EUR_per_MW', 'savings_EUR'],
    rows.map(r => [
      r.name, r.carrier ?? '',
      r.initial, r.optimal, r.retired,
      r.max_hours, r.retired_energy, r.build_year,
      r.capital_cost, r.savings,
    ]),
  )
  return (
    <SubTable title="Storage Units" color={RETIRED_COLOR} count={rows.length} onExport={exportCSV}>
      <thead className="sticky top-0 bg-bg-2 z-10">
        <tr className="border-b border-border">
          <Th>Name</Th><Th>Carrier</Th>
          <Th align="right">Initial</Th><Th align="right">Retired</Th><Th align="right">Remaining</Th>
          <Th align="right">Hours</Th>
          <Th align="right">Energy retired</Th>
          <Th align="right">Build year</Th>
          <Th align="right">Savings</Th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <RowBg i={i} key={`${r.name}@${r.build_year}`}>
            <Td mono>{r.name}</Td>
            <Td><span className="text-muted">{r.carrier ?? ''}</span></Td>
            <Td align="right" mono>{fmtPower(r.initial, 1)}</Td>
            <Td align="right" mono color={RETIRED_COLOR}>−{fmtPower(r.retired, 1)}</Td>
            <Td align="right" mono>{fmtPower(r.optimal, 1)}</Td>
            <Td align="right" mono>{r.max_hours ? r.max_hours.toFixed(1) : '—'}</Td>
            <Td align="right" mono>{fmtEnergy(r.retired_energy, 1)}</Td>
            <Td align="right" mono>{r.build_year || '—'}</Td>
            <Td align="right" mono bold>{fmtCurrency(r.savings, 1)}</Td>
          </RowBg>
        ))}
      </tbody>
    </SubTable>
  )
}

function RetirementStoresTable({ rows }: { rows: RetiredRowBase[] }) {
  const exportCSV = () => downloadCSV(
    'results_stores_retired.csv',
    ['name', 'carrier', 'initial_MWh', 'optimal_MWh', 'retired_MWh',
     'build_year', 'annualised_capital_cost_EUR_per_MWh', 'savings_EUR'],
    rows.map(r => [r.name, r.carrier ?? '', r.initial, r.optimal, r.retired, r.build_year, r.capital_cost, r.savings]),
  )
  return (
    <SubTable title="Stores" color={RETIRED_COLOR} count={rows.length} onExport={exportCSV}>
      <thead className="sticky top-0 bg-bg-2 z-10">
        <tr className="border-b border-border">
          <Th>Name</Th><Th>Carrier</Th>
          <Th align="right">Initial</Th><Th align="right">Retired</Th><Th align="right">Remaining</Th>
          <Th align="right">Build year</Th>
          <Th align="right">Savings</Th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <RowBg i={i} key={`${r.name}@${r.build_year}`}>
            <Td mono>{r.name}</Td>
            <Td><span className="text-muted">{r.carrier ?? ''}</span></Td>
            <Td align="right" mono>{fmtEnergy(r.initial, 1)}</Td>
            <Td align="right" mono color={RETIRED_COLOR}>−{fmtEnergy(r.retired, 1)}</Td>
            <Td align="right" mono>{fmtEnergy(r.optimal, 1)}</Td>
            <Td align="right" mono>{r.build_year || '—'}</Td>
            <Td align="right" mono bold>{fmtCurrency(r.savings, 1)}</Td>
          </RowBg>
        ))}
      </tbody>
    </SubTable>
  )
}

function RetirementLinksTable({ rows }: { rows: RetiredRowBase[] }) {
  const exportCSV = () => downloadCSV(
    'results_links_retired.csv',
    ['name', 'carrier', 'initial_MW', 'optimal_MW', 'retired_MW',
     'build_year', 'annualised_capital_cost_EUR_per_MW', 'savings_EUR'],
    rows.map(r => [r.name, r.carrier ?? '', r.initial, r.optimal, r.retired, r.build_year, r.capital_cost, r.savings]),
  )
  return (
    <SubTable title="Links" color={RETIRED_COLOR} count={rows.length} onExport={exportCSV}>
      <thead className="sticky top-0 bg-bg-2 z-10">
        <tr className="border-b border-border">
          <Th>Name</Th><Th>Carrier</Th>
          <Th align="right">Initial</Th><Th align="right">Retired</Th><Th align="right">Remaining</Th>
          <Th align="right">Build year</Th>
          <Th align="right">Savings</Th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <RowBg i={i} key={`${r.name}@${r.build_year}`}>
            <Td mono>{r.name}</Td>
            <Td><span className="text-muted">{r.carrier ?? ''}</span></Td>
            <Td align="right" mono>{fmtPower(r.initial, 1)}</Td>
            <Td align="right" mono color={RETIRED_COLOR}>−{fmtPower(r.retired, 1)}</Td>
            <Td align="right" mono>{fmtPower(r.optimal, 1)}</Td>
            <Td align="right" mono>{r.build_year || '—'}</Td>
            <Td align="right" mono bold>{fmtCurrency(r.savings, 1)}</Td>
          </RowBg>
        ))}
      </tbody>
    </SubTable>
  )
}
