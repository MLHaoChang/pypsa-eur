import { useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip as RTooltip, Legend, Cell,
} from 'recharts'
import { ChevronRight, ChevronDown } from 'lucide-react'
import {
  resultsApi,
  type GeneratorEconomicsRow,
  type StorageUnitEconomicsRow,
  type StoreEconomicsRow,
} from '../../api/simulation'
import {
  KPI, ChartCard, ChartActions, Seg, fmtCurrency, fmtEnergy, downloadCSV,
  CHART_GRID, CHART_AXIS, CHART_TOOLTIP, CHART_LEGEND, yAxisLabel,
  isRenewableCarrier, colourForCarrier,
} from './shared'
import { useResultsFilter } from './filterContext'
import { CarrierFilter, useCarrierFilter, bindCarrierFilter } from './CarrierFilter'
import { canonicaliseCarrier, type ComponentClass } from './carrierAliases'

// ── Economics tab ──────────────────────────────────────────────────────────
// Per-asset profitability view. Each row is one Generator / StorageUnit / Store
// with its annualised revenue (production × bus marginal price), fixed cost
// (capital_cost × p_nom_opt, already annualised), variable cost (marginal_cost
// × dispatch), and the net profit + LCOE/LCOS that fall out.
//
// Two-level drill-down:
//   1. By group (Thermal / Renewables / Storage) — quick portfolio summary.
//   2. Expand a group → individual assets — for asset-level LCOE/LCOS.
//   3. Expand an asset → per-period breakdown (multi-period only).
//
// All numbers come from /api/results/asset_economics — see the backend
// docstring for the exact formulas. Weighting is the same convention as
// elsewhere in Results (snapshot_weightings × investment_period_weightings.years).

type GroupKey = 'Thermal' | 'Renewables' | 'Storage'

interface AggregatedAssetRow {
  group: GroupKey
  name: string
  carrier: string
  bus: string
  capacity_label: string         // e.g. "1.20 GW" or "200 MWh"
  energy_mwh: number             // for generators: energy dispatched; storage: discharged
  charge_mwh: number             // 0 for generators; storage charge MWh — needed for group spread
  revenue_eur: number            // for storage: discharge_revenue
  charge_cost_eur: number        // 0 for generators
  vom_cost_eur: number
  fixed_cost_eur: number
  fom_cost_eur: number
  net_profit_eur: number
  lcoe_or_lcos: number | null    // €/MWh; LCOE for generators, LCOS for storage
  spread_or_avg_price: number | null  // discharge−charge spread for storage; avg price for generators
  by_period: Array<{
    period: number | string
    energy_mwh: number           // discharge MWh for storage, energy for generators
    charge_mwh: number           // exact per-period charge MWh for storage; 0 for generators
    revenue_eur: number          // discharge revenue for storage
    charge_cost_eur: number
    vom_cost_eur: number
    fixed_cost_eur: number
    fom_cost_eur: number
    net_profit_eur: number
    lcoe_or_lcos: number | null
  }>
}

// Map an asset row from the API into the row shape used by the table — the
// generator/storage/store flavours have slightly different field names, but
// the UI columns are common.
function makeGenRow(g: GeneratorEconomicsRow): AggregatedAssetRow {
  return {
    group: isRenewableCarrier(g.carrier) ? 'Renewables' : 'Thermal',
    name: g.name,
    carrier: g.carrier,
    bus: g.bus,
    capacity_label: g.p_nom_opt_mw >= 1000
      ? `${(g.p_nom_opt_mw / 1000).toFixed(2)} GW`
      : `${g.p_nom_opt_mw.toFixed(1)} MW`,
    energy_mwh: g.energy_mwh,
    charge_mwh: 0,
    revenue_eur: g.revenue_eur,
    charge_cost_eur: 0,
    vom_cost_eur: g.vom_cost_eur,
    fixed_cost_eur: g.fixed_cost_eur,
    fom_cost_eur: g.fom_cost_eur,
    net_profit_eur: g.net_profit_eur,
    lcoe_or_lcos: g.lcoe_eur_per_mwh,
    spread_or_avg_price: g.avg_price_eur_per_mwh,
    by_period: g.by_period.map(p => ({
      period: p.period,
      energy_mwh: p.energy_mwh,
      charge_mwh: 0,
      revenue_eur: p.revenue_eur,
      charge_cost_eur: 0,
      vom_cost_eur: p.vom_cost_eur,
      fixed_cost_eur: p.fixed_cost_eur,
      fom_cost_eur: p.fom_cost_eur,
      net_profit_eur: p.net_profit_eur,
      lcoe_or_lcos: p.lcoe_eur_per_mwh,
    })),
  }
}
function makeSURow(s: StorageUnitEconomicsRow): AggregatedAssetRow {
  return {
    group: 'Storage',
    name: s.name,
    carrier: s.carrier,
    bus: s.bus,
    capacity_label: `${s.p_nom_opt_mw.toFixed(1)} MW · ${s.energy_capacity_mwh.toFixed(0)} MWh`,
    energy_mwh: s.discharge_mwh,
    charge_mwh: s.charge_mwh,
    revenue_eur: s.discharge_revenue_eur,
    charge_cost_eur: s.charge_cost_eur,
    vom_cost_eur: s.vom_cost_eur,
    fixed_cost_eur: s.fixed_cost_eur,
    fom_cost_eur: s.fom_cost_eur,
    net_profit_eur: s.net_profit_eur,
    lcoe_or_lcos: s.lcos_eur_per_mwh,
    spread_or_avg_price: s.spread_eur_per_mwh,
    by_period: s.by_period.map(p => ({
      period: p.period,
      energy_mwh: p.discharge_mwh,
      charge_mwh: p.charge_mwh,
      revenue_eur: p.discharge_revenue_eur,
      charge_cost_eur: p.charge_cost_eur,
      vom_cost_eur: p.vom_cost_eur,
      fixed_cost_eur: p.fixed_cost_eur,
      fom_cost_eur: p.fom_cost_eur,
      net_profit_eur: p.net_profit_eur,
      lcoe_or_lcos: p.lcos_eur_per_mwh,
    })),
  }
}
function makeStoreRow(s: StoreEconomicsRow): AggregatedAssetRow {
  return {
    group: 'Storage',
    name: s.name,
    carrier: s.carrier,
    bus: s.bus,
    capacity_label: `${s.e_nom_opt_mwh.toFixed(0)} MWh`,
    energy_mwh: s.discharge_mwh,
    charge_mwh: s.charge_mwh,
    revenue_eur: s.discharge_revenue_eur,
    charge_cost_eur: s.charge_cost_eur,
    vom_cost_eur: s.vom_cost_eur,
    fixed_cost_eur: s.fixed_cost_eur,
    fom_cost_eur: s.fom_cost_eur,
    net_profit_eur: s.net_profit_eur,
    lcoe_or_lcos: s.lcos_eur_per_mwh,
    spread_or_avg_price: s.spread_eur_per_mwh,
    by_period: s.by_period.map(p => ({
      period: p.period,
      energy_mwh: p.discharge_mwh,
      charge_mwh: p.charge_mwh,
      revenue_eur: p.discharge_revenue_eur,
      charge_cost_eur: p.charge_cost_eur,
      vom_cost_eur: p.vom_cost_eur,
      fixed_cost_eur: p.fixed_cost_eur,
      fom_cost_eur: p.fom_cost_eur,
      net_profit_eur: p.net_profit_eur,
      lcoe_or_lcos: p.lcos_eur_per_mwh,
    })),
  }
}


// Sum rows into a group total — same shape as a single row so the table can
// render group headers and asset rows from the same component.
function sumGroup(rows: AggregatedAssetRow[], group: GroupKey, period?: number | string | null): AggregatedAssetRow {
  // When `period` is non-null we sum the matching by_period entry instead of
  // the row's top-level totals. The capacity label / LCOE come from a
  // weighted aggregation since per-asset capacities differ.
  let energy = 0, charge = 0, revenue = 0, charge_cost = 0, vom = 0, fixed = 0, fom = 0, net = 0
  let lcoe_num = 0, lcoe_denom = 0
  for (const r of rows) {
    if (period != null) {
      const pe = r.by_period.find(p => p.period === period)
      if (!pe) continue
      energy += pe.energy_mwh
      revenue += pe.revenue_eur
      charge_cost += pe.charge_cost_eur
      // Exact per-period charge MWh from the backend payload.
      charge += pe.charge_mwh ?? 0
      vom += pe.vom_cost_eur
      fixed += pe.fixed_cost_eur
      fom += pe.fom_cost_eur
      net += pe.net_profit_eur
      lcoe_num += pe.fixed_cost_eur + pe.vom_cost_eur + pe.charge_cost_eur
      lcoe_denom += pe.energy_mwh
    } else {
      energy += r.energy_mwh
      charge += r.charge_mwh
      revenue += r.revenue_eur
      charge_cost += r.charge_cost_eur
      vom += r.vom_cost_eur
      fixed += r.fixed_cost_eur
      fom += r.fom_cost_eur
      net += r.net_profit_eur
      lcoe_num += r.fixed_cost_eur + r.vom_cost_eur + r.charge_cost_eur
      lcoe_denom += r.energy_mwh
    }
  }
  // For storage groups: spread = avg_discharge_price − avg_charge_price.
  // For generator groups: avg_price = revenue / energy.
  let spread: number | null = null
  if (group === 'Storage') {
    if (revenue > 0 && energy > 1e-6 && charge_cost !== 0 && charge > 1e-6) {
      spread = (revenue / energy) - (charge_cost / charge)
    } else if (revenue > 0 && energy > 1e-6) {
      // No charging observed — falls back to avg discharge price (still
      // a useful headline number).
      spread = revenue / energy
    }
  } else {
    spread = revenue > 0 && energy > 0 ? revenue / energy : null
  }
  return {
    group,
    name: '__group__',
    carrier: '',
    bus: '',
    capacity_label: `${rows.length} asset${rows.length === 1 ? '' : 's'}`,
    energy_mwh: energy,
    charge_mwh: charge,
    revenue_eur: revenue,
    charge_cost_eur: charge_cost,
    vom_cost_eur: vom,
    fixed_cost_eur: fixed,
    fom_cost_eur: fom,
    net_profit_eur: net,
    lcoe_or_lcos: lcoe_denom > 1e-6 ? lcoe_num / lcoe_denom : null,
    spread_or_avg_price: spread,
    by_period: [],
  }
}

function ProfitChip({ value }: { value: number }) {
  // Coloured chip used in net-profit cells: green for positive, red for
  // negative, neutral for ~zero. The colour makes scanning a table of
  // dozens of generators much faster than reading every number.
  if (Math.abs(value) < 1) {
    return <span className="font-mono text-[11px] text-muted">{fmtCurrency(value)}</span>
  }
  return (
    <span
      className={`font-mono text-[11px] font-medium ${
        value > 0 ? 'text-success' : 'text-danger'
      }`}
    >
      {fmtCurrency(value)}
    </span>
  )
}

export default function Economics() {
  const filter = useResultsFilter()
  const { data: payload, isLoading } = useQuery({
    queryKey: ['results', 'asset_economics'],
    queryFn: resultsApi.getAssetEconomics,
  })
  // Refs for SVG / CSV export on the per-asset bar charts.
  const profitChartRef = useRef<HTMLDivElement | null>(null)
  const lcoeChartRef   = useRef<HTMLDivElement | null>(null)
  // Per-asset "Sort by" control on the table.
  const [sortKey, setSortKey] = useState<
    'net_profit' | 'revenue' | 'energy' | 'lcoe' | 'fixed_cost' | 'capacity'
  >('net_profit')
  const [sortDesc, setSortDesc] = useState<boolean>(true)
  // Group expansion state — start with everything collapsed so the page
  // doesn't dump 300 rows at once.
  const [openGroups, setOpenGroups] = useState<Set<GroupKey>>(new Set())
  // Asset expansion state for per-period drill-down (only useful when
  // the active run has multiple periods).
  const [openAssets, setOpenAssets] = useState<Set<string>>(new Set())
  // Layout toggle between "group-level summary" and "every individual asset".
  const [view, setView] = useState<'groups' | 'assets'>('groups')

  // Unfiltered list of every asset row. Used to derive the carrier filter's
  // option list AND as the source for `allRows` below (which applies the
  // active filter). Keeping the unfiltered list around means a "filter
  // changed to Heat → no heat assets" never shrinks the carrier strip
  // itself, only the downstream data.
  const everyRow = useMemo<AggregatedAssetRow[]>(() => {
    if (!payload) return []
    return [
      ...payload.generators.map(makeGenRow),
      ...payload.storage_units.map(makeSURow),
      ...payload.stores.map(makeStoreRow),
    ]
  }, [payload])

  // ── Carrier filter ─────────────────────────────────────────────────────
  // Scopes the entire page (KPIs, top-profit chart, LCOE bars, group table,
  // per-asset table) to one carrier. Filtering at the `allRows` level
  // cascades through every memo that derives from it, so each downstream
  // figure stays internally consistent without per-derivation guards.
  //
  // Each row's raw carrier goes through the cross-tab alias map BEFORE
  // chip generation / filter comparison — so `BESS` / `Li-Ion` / `battery`
  // all collapse into the same "Battery" chip and selecting "Battery" on
  // Economics shows the same physical assets the Dispatch tab groups
  // under "Battery". Component class is derived from the row's group
  // ('Storage' → StorageUnit semantics, everything else → Generator).
  const rowComponentClass = (r: AggregatedAssetRow): ComponentClass =>
    r.group === 'Storage' ? 'StorageUnit' : 'Generator'
  const availableCarriers = useMemo(() => {
    const seen = new Set<string>()
    for (const r of everyRow) seen.add(canonicaliseCarrier(rowComponentClass(r), r.carrier))
    return [...seen].sort((a, b) => {
      if (a === 'unspecified' && b !== 'unspecified') return 1
      if (b === 'unspecified' && a !== 'unspecified') return -1
      return a.localeCompare(b)
    })
  }, [everyRow])
  const carrierFilter = useCarrierFilter({
    availableCarriers,
    storageKey: 'results:economics:carrier',
  })
  const allRows = useMemo<AggregatedAssetRow[]>(() => {
    if (!carrierFilter.isFiltered) return everyRow
    return everyRow.filter(r => carrierFilter.matchesSelected(canonicaliseCarrier(rowComponentClass(r), r.carrier)))
  }, [everyRow, carrierFilter])

  // Group → list of asset rows. Filtered by the active period (when set),
  // since per-period totals come from `row.by_period[period]` instead of the
  // row's top-level totals.
  const groupedRows = useMemo(() => {
    const out: Record<GroupKey, AggregatedAssetRow[]> = {
      Thermal: [], Renewables: [], Storage: [],
    }
    for (const r of allRows) out[r.group].push(r)
    return out
  }, [allRows])

  // Effective "row" for display. When a period is selected, we project each
  // asset row onto its `by_period[period]` entry so the table reflects the
  // single-period view. When period is null (Aggregated), we use the row's
  // horizon-wide totals.
  const projectedRows = useMemo(() => {
    if (filter.selectedPeriod == null) return allRows
    return allRows.map((r): AggregatedAssetRow => {
      const pe = r.by_period.find(p => p.period === filter.selectedPeriod)
      if (!pe) {
        // Asset has no entry for this period — return a zeroed row so
        // visibility is preserved (don't silently drop assets).
        return {
          ...r,
          energy_mwh: 0, charge_mwh: 0, revenue_eur: 0, charge_cost_eur: 0,
          vom_cost_eur: 0, fixed_cost_eur: 0, fom_cost_eur: 0,
          net_profit_eur: 0,
          lcoe_or_lcos: null, spread_or_avg_price: null,
        }
      }
      // Exact per-period charge_mwh from the backend's by_period entry
      // (was previously approximated via discharge_mwh ratio; backend
      // now emits the exact value so we drop the approximation).
      return {
        ...r,
        energy_mwh: pe.energy_mwh,
        charge_mwh: pe.charge_mwh ?? 0,
        revenue_eur: pe.revenue_eur,
        charge_cost_eur: pe.charge_cost_eur,
        vom_cost_eur: pe.vom_cost_eur,
        fixed_cost_eur: pe.fixed_cost_eur,
        fom_cost_eur: pe.fom_cost_eur,
        net_profit_eur: pe.net_profit_eur,
        lcoe_or_lcos: pe.lcoe_or_lcos,
        spread_or_avg_price: pe.energy_mwh > 0 ? pe.revenue_eur / pe.energy_mwh : null,
      }
    })
  }, [allRows, filter.selectedPeriod])

  const sortedAssets = useMemo(() => {
    const arr = [...projectedRows]
    const getKey = (r: AggregatedAssetRow): number => {
      switch (sortKey) {
        case 'net_profit': return r.net_profit_eur
        case 'revenue':    return r.revenue_eur
        case 'energy':     return r.energy_mwh
        case 'lcoe':       return r.lcoe_or_lcos ?? Number.NEGATIVE_INFINITY
        case 'fixed_cost': return r.fixed_cost_eur
        case 'capacity':   return r.energy_mwh    // proxy — capacity_label is a string
        default:           return 0
      }
    }
    arr.sort((a, b) => {
      const ka = getKey(a), kb = getKey(b)
      return sortDesc ? kb - ka : ka - kb
    })
    return arr
  }, [projectedRows, sortKey, sortDesc])

  // Top KPIs — horizon-wide totals (or selected-period totals when filtered).
  const kpis = useMemo(() => {
    let revenue = 0, charge_cost = 0, vom = 0, fixed = 0, fom = 0, energy = 0
    let profitable = 0, unprofitable = 0
    for (const r of projectedRows) {
      revenue += r.revenue_eur
      charge_cost += r.charge_cost_eur
      vom += r.vom_cost_eur
      fixed += r.fixed_cost_eur
      fom += r.fom_cost_eur
      energy += r.energy_mwh
      if (r.net_profit_eur > 1) profitable += 1
      else if (r.net_profit_eur < -1) unprofitable += 1
    }
    const net = revenue - charge_cost - vom - fixed
    // System-level LCOE = (Σ fixed + Σ vom + Σ charge_cost) / Σ energy.
    // For generators charge_cost is 0; for storage it's the discharged-MWh
    // weighted purchase cost. Useful for comparing whole-portfolio €/MWh.
    const sysLcoe = energy > 1e-6 ? (fixed + vom + charge_cost) / energy : null
    return { revenue, charge_cost, vom, fixed, fom, energy, net, profitable, unprofitable, sysLcoe }
  }, [projectedRows])

  const groupTotals = useMemo(() => {
    return {
      Thermal:    sumGroup(groupedRows.Thermal,    'Thermal',    filter.selectedPeriod),
      Renewables: sumGroup(groupedRows.Renewables, 'Renewables', filter.selectedPeriod),
      Storage:    sumGroup(groupedRows.Storage,    'Storage',    filter.selectedPeriod),
    }
  }, [groupedRows, filter.selectedPeriod])

  // ── Chart data ────────────────────────────────────────────────────────
  // Top-10 by net profit (positive bars) + bottom-10 by net loss (negative
  // bars), combined into one chart. Gives the user instant visibility into
  // who's earning and who's bleeding.
  const profitChartData = useMemo(() => {
    const top = sortedAssets
      .filter(r => r.net_profit_eur > 0)
      .slice(0, 10)
      .map(r => ({ name: r.name, value: r.net_profit_eur, carrier: r.carrier, group: r.group }))
    const bottom = [...sortedAssets]
      .filter(r => r.net_profit_eur < 0)
      .sort((a, b) => a.net_profit_eur - b.net_profit_eur)  // most-negative first
      .slice(0, 10)
      .map(r => ({ name: r.name, value: r.net_profit_eur, carrier: r.carrier, group: r.group }))
    return [...top, ...bottom]
  }, [sortedAssets])

  // LCOE / LCOS bars per asset, sorted ascending so cheapest at the top.
  // Hide assets without a defined LCOE (zero-energy → undefined ratio).
  const lcoeChartData = useMemo(() => {
    return sortedAssets
      .filter(r => r.lcoe_or_lcos != null && r.energy_mwh > 1e-6)
      .map(r => ({
        name: r.name,
        value: r.lcoe_or_lcos!,
        carrier: r.carrier,
        group: r.group,
        kind: r.group === 'Storage' ? 'LCOS' : 'LCOE',
      }))
      .sort((a, b) => a.value - b.value)
      .slice(0, 25)
  }, [sortedAssets])

  // ── CSV export ────────────────────────────────────────────────────────
  // Single export covering every asset + every period (long-form), plus the
  // current view's columns. Long-form makes the file easy to pivot in Excel.
  const onExportCSV = () => {
    const rows: unknown[][] = []
    for (const r of allRows) {
      // Horizon-wide row first.
      rows.push([
        r.group, r.name, r.carrier, r.bus, 'Total',
        r.energy_mwh.toFixed(2),
        r.revenue_eur.toFixed(2),
        r.charge_cost_eur.toFixed(2),
        r.vom_cost_eur.toFixed(2),
        r.fixed_cost_eur.toFixed(2),
        r.fom_cost_eur.toFixed(2),
        r.net_profit_eur.toFixed(2),
        r.lcoe_or_lcos != null ? r.lcoe_or_lcos.toFixed(4) : '',
        r.spread_or_avg_price != null ? r.spread_or_avg_price.toFixed(4) : '',
      ])
      // One row per period, when available.
      for (const pe of r.by_period) {
        rows.push([
          r.group, r.name, r.carrier, r.bus, String(pe.period),
          pe.energy_mwh.toFixed(2),
          pe.revenue_eur.toFixed(2),
          pe.charge_cost_eur.toFixed(2),
          pe.vom_cost_eur.toFixed(2),
          pe.fixed_cost_eur.toFixed(2),
          pe.fom_cost_eur.toFixed(2),
          pe.net_profit_eur.toFixed(2),
          pe.lcoe_or_lcos != null ? pe.lcoe_or_lcos.toFixed(4) : '',
          '',
        ])
      }
    }
    downloadCSV(
      'asset_economics.csv',
      [
        'group', 'asset', 'carrier', 'bus', 'period',
        'energy_mwh', 'revenue_eur', 'charge_cost_eur',
        'vom_cost_eur', 'fixed_cost_eur', 'fom_cost_eur',
        'net_profit_eur', 'lcoe_lcos_eur_per_mwh', 'spread_or_avg_price_eur_per_mwh',
      ],
      rows,
    )
  }

  // ── Render ────────────────────────────────────────────────────────────
  if (isLoading) {
    return <div className="p-6 text-center text-xs text-muted">Loading…</div>
  }
  if (!payload) {
    return (
      <div className="p-6 text-center text-xs text-muted">
        No solved results. Run a LOPF / SCLOPF (header → <span className="font-medium text-text">Run LOPF</span>) — asset economics need post-solve dispatch + bus marginal prices.
      </div>
    )
  }
  if (allRows.length === 0) {
    return (
      <div className="p-6 text-center text-xs text-muted">
        The solved network has no generators, storage units, or stores to evaluate.
      </div>
    )
  }

  const periodLabel = filter.selectedPeriod != null ? ` (period ${filter.selectedPeriod})` : ''

  return (
    <div className="flex flex-col gap-5 p-5 overflow-y-auto h-full text-sm [&>*]:shrink-0">

      {/* ── KPI strip ──────────────────────────────────────────────────── */}
      <section>
        <div className="flex items-center justify-between mb-2 gap-3 flex-wrap">
          <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">
            Portfolio economics{periodLabel}
            {carrierFilter.isFiltered && (
              <span className="ml-2 text-[10.5px] font-normal text-muted">
                · filtered to {carrierFilter.selectedCarrier}
              </span>
            )}
          </h3>
          <div className="flex items-center gap-3 flex-wrap">
            <CarrierFilter {...bindCarrierFilter(carrierFilter, availableCarriers)} label="Carrier" />
            <span className="text-[10px] text-muted">
              {kpis.profitable} profitable · {kpis.unprofitable} loss-making · {allRows.length} total
            </span>
          </div>
        </div>
        <div className="grid gap-3 grid-cols-2 md:grid-cols-4">
          <KPI accent label="Total revenue"
               value={fmtCurrency(kpis.revenue)}
               sub="Σ dispatch × bus marginal price"
               hint="Annualised market revenue across every generator, storage unit and store. Storage uses discharge revenue only (charge cost is booked separately)." />
          <KPI label="Fixed cost"
               value={fmtCurrency(kpis.fixed)}
               sub="annualised CAPEX + FOM"
               hint="Σ capital_cost × p_nom_opt. PyPSA's capital_cost is already annualised (overnight × annuity), so this matches the LP-objective contribution." />
          <KPI label="Variable cost"
               value={fmtCurrency(kpis.vom + kpis.charge_cost)}
               sub={kpis.charge_cost > 0
                 ? `VOM ${fmtCurrency(kpis.vom)} + charge ${fmtCurrency(kpis.charge_cost)}`
                 : 'Σ dispatch × marginal_cost'}
               hint="Σ |p| × marginal_cost. For storage assets this also includes the cost of charging energy (charge_mwh × bus price) — the market price paid to fill the reservoir." />
          <KPI label="Net profit"
               value={fmtCurrency(kpis.net)}
               sub={`= revenue − ${kpis.charge_cost > 0 ? 'charge − ' : ''}vom − fixed`}
               deltaState={kpis.net > 0 ? 'ok' : kpis.net < 0 ? 'err' : undefined}
               hint="Sum of per-asset net profits. Negative means the portfolio doesn't recover its annualised costs at market prices (typical for must-run assets needed for reliability, not arbitrage)." />
        </div>
        <div className="mt-3 grid gap-3 grid-cols-2 md:grid-cols-4">
          <KPI label="Energy delivered"
               value={fmtEnergy(kpis.energy)}
               sub="MWh sold into the market (Σ p+ × weight)"
               hint="Σ dispatch × weight. For storage, this is the discharged energy (positive p only) — charge MWh isn't double-counted." />
          <KPI label="Charge cost (storage)"
               value={fmtCurrency(kpis.charge_cost)}
               sub="market price paid to fill"
               hint="Σ charge_mwh × bus price across every storage asset. Zero on networks without storage. Subtracts directly from storage revenue in the net-profit roll-up." />
          <KPI label="System LCOE / LCOS"
               value={kpis.sysLcoe != null ? `${kpis.sysLcoe.toFixed(2)} €/MWh` : '—'}
               sub="(Σ fixed + vom + charge) / Σ energy"
               hint="Portfolio-wide €/MWh — what every MWh delivered into the market costs the system to produce, on average. Useful as a yardstick: the avg market price needs to exceed this for the portfolio to recover costs." />
          <KPI label="FOM (informational)"
               value={fmtCurrency(kpis.fom)}
               sub="user-typed fom_cost × p_nom_opt"
               hint="Reads the asset's fom_cost field × installed capacity. Already part of fixed cost when the user encoded it that way; surfaced here so you can see what fraction of fixed cost is FOM vs annualised CAPEX." />
        </div>
      </section>

      {/* ── Top profit / loss chart ───────────────────────────────────── */}
      {profitChartData.length > 0 && (
        <ChartCard
          title="Top profitable & loss-making assets"
          hint="Bar height = net profit; green > 0, red < 0."
          right={<ChartActions chartRef={profitChartRef} onExportCSV={onExportCSV} filename={`asset_economics_profit${filter.selectedPeriod != null ? `_${filter.selectedPeriod}` : ''}${carrierFilter.isFiltered ? `_${carrierFilter.selectedCarrier}` : ''}`} />}
        >
          <div ref={profitChartRef}>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={profitChartData} margin={{ top: 5, right: 20, left: 30, bottom: 60 }}>
                <CartesianGrid {...CHART_GRID} />
                <XAxis dataKey="name" {...CHART_AXIS} angle={-30} textAnchor="end" height={80} interval={0} />
                <YAxis {...CHART_AXIS} label={yAxisLabel('€')}
                  tickFormatter={(v: number) =>
                    Math.abs(v) >= 1e9 ? `${(v / 1e9).toFixed(1)}B` :
                    Math.abs(v) >= 1e6 ? `${(v / 1e6).toFixed(1)}M` :
                    Math.abs(v) >= 1e3 ? `${(v / 1e3).toFixed(1)}k` : `${v.toFixed(0)}`} />
                <RTooltip {...CHART_TOOLTIP}
                  formatter={(v: number) => [fmtCurrency(v), 'Net profit']} />
                <Bar dataKey="value">
                  {profitChartData.map((row, i) => (
                    <Cell key={i} fill={row.value > 0 ? 'var(--color-success)' : 'var(--color-danger)'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>
      )}

      {/* ── LCOE / LCOS bar chart ─────────────────────────────────────── */}
      {lcoeChartData.length > 0 && (
        <ChartCard
          title="Per-asset LCOE / LCOS"
          hint="Ranked low → high. Storage shows LCOS (includes the cost of charging electricity)."
          right={<ChartActions chartRef={lcoeChartRef} onExportCSV={onExportCSV} filename={`asset_lcoe_lcos${filter.selectedPeriod != null ? `_${filter.selectedPeriod}` : ''}${carrierFilter.isFiltered ? `_${carrierFilter.selectedCarrier}` : ''}`} />}
        >
          <div ref={lcoeChartRef}>
            <ResponsiveContainer width="100%" height={Math.max(300, lcoeChartData.length * 22)}>
              <BarChart data={lcoeChartData} layout="vertical" margin={{ top: 5, right: 30, left: 100, bottom: 5 }}>
                <CartesianGrid {...CHART_GRID} />
                <XAxis type="number" {...CHART_AXIS} label={yAxisLabel('€/MWh')} />
                <YAxis dataKey="name" type="category" width={150}
                  tick={{ fontSize: 9, fill: 'var(--color-axis)' }}
                  stroke="var(--color-grid)" tickLine={false} />
                <RTooltip {...CHART_TOOLTIP}
                  formatter={(v: number, _n: string, ctx: { payload?: { kind?: string } }) =>
                    [`${v.toFixed(2)} €/MWh`, ctx.payload?.kind ?? 'LCOE']} />
                <Legend {...CHART_LEGEND} />
                <Bar dataKey="value" name="€/MWh">
                  {lcoeChartData.map((row, i) => (
                    <Cell key={i} fill={colourForCarrier(row.carrier, i)} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>
      )}

      {/* ── Per-asset / per-group table ───────────────────────────────── */}
      <ChartCard
        title={view === 'groups' ? 'By asset group' : 'By individual asset'}
        hint={view === 'groups' ? 'Click a group to expand individual assets.' : 'Click an asset to expand per-period breakdown.'}
        count={view === 'groups' ? 3 : sortedAssets.length}
        right={
          <div className="flex items-center gap-3">
            <Seg
              value={view}
              onChange={setView}
              options={[
                { value: 'groups', label: 'Groups' },
                { value: 'assets', label: 'Assets' },
              ]}
            />
            {view === 'assets' && (
              <Seg
                value={sortKey}
                onChange={setSortKey}
                options={[
                  { value: 'net_profit', label: 'Net profit' },
                  { value: 'lcoe',       label: 'LCOE/LCOS' },
                  { value: 'revenue',    label: 'Revenue' },
                  { value: 'energy',     label: 'Energy' },
                ]}
              />
            )}
            {view === 'assets' && (
              <button
                onClick={() => setSortDesc(s => !s)}
                title="Toggle ascending / descending"
                className="text-[10px] text-muted hover:text-text px-1"
              >
                {sortDesc ? '↓ desc' : '↑ asc'}
              </button>
            )}
          </div>
        }
      >
        <div className="border border-border rounded overflow-auto max-h-[600px]">
          <table className="w-full text-xs border-collapse" style={{ minWidth: 1100 }}>
            <thead className="sticky top-0 bg-bg-2 z-10">
              <tr className="border-b border-border">
                <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Asset</th>
                <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Carrier</th>
                <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Capacity</th>
                <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Energy</th>
                <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Revenue</th>
                <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase" title="Storage only — cost to charge from the market.">Charge cost</th>
                <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase" title="Σ |p| × marginal_cost.">VOM</th>
                <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase" title="Annualised CAPEX + FOM (capital_cost × p_nom_opt).">Fixed</th>
                <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase">Net profit</th>
                <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase" title="LCOE for generators / LCOS for storage.">LCOE/LCOS</th>
                <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase" title="Avg price for generators (€/MWh sold); discharge−charge spread for storage.">Spread / avg €</th>
              </tr>
            </thead>
            <tbody>
              {view === 'groups' ? (
                (['Renewables', 'Thermal', 'Storage'] as GroupKey[]).map(g => {
                  const total = groupTotals[g]
                  const rows = groupedRows[g]
                  if (rows.length === 0) return null
                  const open = openGroups.has(g)
                  return (
                    <GroupSection
                      key={g}
                      groupKey={g}
                      open={open}
                      onToggle={() => {
                        setOpenGroups(s => {
                          const n = new Set(s); n.has(g) ? n.delete(g) : n.add(g); return n
                        })
                      }}
                      total={total}
                      assets={
                        // When the group is open, respect the active period
                        // projection (so the inner rows match the strip).
                        filter.selectedPeriod != null
                          ? rows.map(r => projectedRows.find(p => p.name === r.name) ?? r)
                          : rows
                      }
                      openAssets={openAssets}
                      setOpenAssets={setOpenAssets}
                      isMultiPeriod={payload.is_multi_period}
                      selectedPeriod={filter.selectedPeriod}
                    />
                  )
                })
              ) : (
                sortedAssets.map(r => {
                  const aOpen = openAssets.has(r.name)
                  return (
                    <AssetRow
                      key={r.name}
                      row={r}
                      open={aOpen}
                      onToggle={() => {
                        setOpenAssets(s => {
                          const n = new Set(s); n.has(r.name) ? n.delete(r.name) : n.add(r.name); return n
                        })
                      }}
                      isMultiPeriod={payload.is_multi_period}
                      // When period is selected, hide the period-rollout
                      // since we're already viewing one period.
                      showByPeriod={filter.selectedPeriod == null}
                    />
                  )
                })
              )}
            </tbody>
          </table>
        </div>
      </ChartCard>

      {/* ── Hydrogen LCOH (Link-based electrolysers) ──────────────────── */}
      {/* Moved here from above the per-asset table so the user reads
          electrolyser link economics immediately alongside the gen/storage
          economics. Same time basis (horizon-scaled CAPEX) so the €/MWh
          numbers are directly comparable to LCOS/LCOE rows above. */}
      <LcohSection />

      {/* ── Methodology note ──────────────────────────────────────────── */}
      <details className="border border-border rounded text-[11px]">
        <summary className="px-3 py-2 cursor-pointer text-muted hover:text-text">
          Methodology — how revenue, profit, LCOE and LCOS are computed
        </summary>
        <div className="px-3 py-2 space-y-2 text-text/80 leading-relaxed">
          <p>
            <span className="font-mono">revenue</span> = Σₜ p[t] × marginal_price[bus, t] × weight[t]. Storage assets only count
            <span className="font-mono"> max(p[t], 0)</span> (discharge); charging power generates
            <span className="font-mono"> charge_cost</span> instead.
          </p>
          <p>
            <span className="font-mono">vom_cost</span> = Σₜ |p[t]| × marginal_cost × weight[t]. For generators the absolute value
            matches the dispatch sign convention; storage applies marginal_cost to discharge only (PyPSA's standard formulation).
          </p>
          <p>
            <span className="font-mono">fixed_cost</span> = capital_cost × p_nom_opt. PyPSA's capital_cost is already annualised
            (overnight × annuity if you typed overnight_cost), so this equals the LP-objective contribution. FOM is included if
            the user folded it in; the separate <span className="font-mono">fom_cost</span> field is shown for reference only.
          </p>
          <p>
            <span className="font-mono">net_profit</span> = revenue − charge_cost − vom_cost − fixed_cost. Negative means the
            asset doesn't recover its annualised costs at market prices — common for must-run thermal or for storage that's
            sized for reliability rather than arbitrage.
          </p>
          <p>
            <span className="font-mono">LCOE</span> (generators) = (fixed_cost + vom_cost) / energy_dispatched.
            <span className="font-mono"> LCOS</span> (storage) = (fixed_cost + vom_cost + charge_cost) / discharge_mwh — every
            MWh delivered into the market has to pay for the asset itself plus the electricity it consumed to fill the reservoir.
          </p>
          <p>
            Weighting: snapshot_weightings.objective × investment_period_weightings.years — same convention used by
            n.statistics() and elsewhere in this Results panel. Multi-period runs distribute fixed_cost across periods in
            proportion to each period's <span className="font-mono">years</span> weight.
          </p>
        </div>
      </details>
    </div>
  )
}

// ── Group section ────────────────────────────────────────────────────────
function GroupSection({
  groupKey, open, onToggle, total, assets, openAssets, setOpenAssets,
  isMultiPeriod, selectedPeriod,
}: {
  groupKey: GroupKey
  open: boolean
  onToggle: () => void
  total: AggregatedAssetRow
  assets: AggregatedAssetRow[]
  openAssets: Set<string>
  setOpenAssets: (updater: (prev: Set<string>) => Set<string>) => void
  isMultiPeriod: boolean
  selectedPeriod: number | string | null
}) {
  return (
    <>
      <tr
        onClick={onToggle}
        className="bg-bg-2 cursor-pointer border-b border-border hover:bg-panel"
      >
        <td className="px-2 py-1.5 font-semibold text-text">
          <div className="flex items-center gap-1.5">
            {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
            {groupKey}
          </div>
        </td>
        <td className="px-2 py-1 text-[10px] text-muted">{assets.length} asset{assets.length === 1 ? '' : 's'}</td>
        <td className="px-2 py-1 text-right text-[10px] text-muted">{total.capacity_label}</td>
        <td className="px-2 py-1 text-right font-mono text-[11px]">{fmtEnergy(total.energy_mwh)}</td>
        <td className="px-2 py-1 text-right font-mono text-[11px]">{fmtCurrency(total.revenue_eur)}</td>
        <td className="px-2 py-1 text-right font-mono text-[11px]">{total.charge_cost_eur > 0 ? fmtCurrency(total.charge_cost_eur) : '—'}</td>
        <td className="px-2 py-1 text-right font-mono text-[11px]">{fmtCurrency(total.vom_cost_eur)}</td>
        <td className="px-2 py-1 text-right font-mono text-[11px]">{fmtCurrency(total.fixed_cost_eur)}</td>
        <td className="px-2 py-1 text-right"><ProfitChip value={total.net_profit_eur} /></td>
        <td className="px-2 py-1 text-right font-mono text-[11px]">
          {total.lcoe_or_lcos != null ? `${total.lcoe_or_lcos.toFixed(2)} €/MWh` : '—'}
        </td>
        <td className="px-2 py-1 text-right font-mono text-[11px]">
          {total.spread_or_avg_price != null ? `${total.spread_or_avg_price.toFixed(2)} €/MWh` : '—'}
        </td>
      </tr>
      {open && assets.map((r, i) => {
        const aOpen = openAssets.has(r.name)
        return (
          <AssetRow
            key={r.name}
            row={r}
            indent
            zebra={i % 2 === 0}
            open={aOpen}
            onToggle={() => setOpenAssets(s => {
              const n = new Set(s); n.has(r.name) ? n.delete(r.name) : n.add(r.name); return n
            })}
            isMultiPeriod={isMultiPeriod}
            showByPeriod={selectedPeriod == null}
          />
        )
      })}
    </>
  )
}

// ── Asset row + (optional) per-period sub-table ───────────────────────────
function AssetRow({
  row, indent = false, zebra = false, open, onToggle, isMultiPeriod, showByPeriod,
}: {
  row: AggregatedAssetRow
  indent?: boolean
  zebra?: boolean
  open: boolean
  onToggle: () => void
  isMultiPeriod: boolean
  showByPeriod: boolean
}) {
  const expandable = isMultiPeriod && showByPeriod && row.by_period.length > 0
  return (
    <>
      <tr
        onClick={expandable ? onToggle : undefined}
        className={`border-b border-border/40 ${zebra ? 'bg-bg' : 'bg-panel/60'}
          ${expandable ? 'cursor-pointer hover:bg-panel' : ''}`}
      >
        <td className={`px-2 py-1 font-mono text-[11px] ${indent ? 'pl-6' : ''}`}>
          <div className="flex items-center gap-1">
            {expandable && (open ? <ChevronDown size={9} className="text-muted" /> : <ChevronRight size={9} className="text-muted" />)}
            <span>{row.name}</span>
          </div>
        </td>
        <td className="px-2 py-1 text-[11px] text-muted">{row.carrier || '—'}</td>
        <td className="px-2 py-1 text-right text-[11px] text-muted">{row.capacity_label}</td>
        <td className="px-2 py-1 text-right font-mono text-[11px]">{fmtEnergy(row.energy_mwh)}</td>
        <td className="px-2 py-1 text-right font-mono text-[11px]">{fmtCurrency(row.revenue_eur)}</td>
        <td className="px-2 py-1 text-right font-mono text-[11px]">{row.charge_cost_eur > 0 ? fmtCurrency(row.charge_cost_eur) : '—'}</td>
        <td className="px-2 py-1 text-right font-mono text-[11px]">{fmtCurrency(row.vom_cost_eur)}</td>
        <td className="px-2 py-1 text-right font-mono text-[11px]">{fmtCurrency(row.fixed_cost_eur)}</td>
        <td className="px-2 py-1 text-right"><ProfitChip value={row.net_profit_eur} /></td>
        <td className="px-2 py-1 text-right font-mono text-[11px]">
          {row.lcoe_or_lcos != null ? `${row.lcoe_or_lcos.toFixed(2)} €/MWh` : '—'}
        </td>
        <td className="px-2 py-1 text-right font-mono text-[11px]">
          {row.spread_or_avg_price != null ? `${row.spread_or_avg_price.toFixed(2)} €/MWh` : '—'}
        </td>
      </tr>
      {expandable && open && (
        <tr className="bg-bg-2/50 border-b border-border/40">
          <td colSpan={11} className="px-4 py-2">
            <div className="text-[10px] text-muted mb-1.5 uppercase tracking-wider">
              Per-period breakdown
            </div>
            <table className="w-full text-[10.5px] border-collapse">
              <thead>
                <tr className="border-b border-border/40">
                  <th className="text-left  px-2 py-0.5 text-muted font-semibold uppercase tracking-wider">Period</th>
                  <th className="text-right px-2 py-0.5 text-muted font-semibold uppercase tracking-wider">Energy</th>
                  <th className="text-right px-2 py-0.5 text-muted font-semibold uppercase tracking-wider">Revenue</th>
                  <th className="text-right px-2 py-0.5 text-muted font-semibold uppercase tracking-wider">Charge cost</th>
                  <th className="text-right px-2 py-0.5 text-muted font-semibold uppercase tracking-wider">VOM</th>
                  <th className="text-right px-2 py-0.5 text-muted font-semibold uppercase tracking-wider">Fixed</th>
                  <th className="text-right px-2 py-0.5 text-muted font-semibold uppercase tracking-wider">Net profit</th>
                  <th className="text-right px-2 py-0.5 text-muted font-semibold uppercase tracking-wider">LCOE/LCOS</th>
                </tr>
              </thead>
              <tbody>
                {row.by_period.map(pe => (
                  <tr key={String(pe.period)} className="border-b border-border/20">
                    <td className="px-2 py-0.5 font-mono">{pe.period}</td>
                    <td className="px-2 py-0.5 text-right font-mono">{fmtEnergy(pe.energy_mwh)}</td>
                    <td className="px-2 py-0.5 text-right font-mono">{fmtCurrency(pe.revenue_eur)}</td>
                    <td className="px-2 py-0.5 text-right font-mono">{pe.charge_cost_eur > 0 ? fmtCurrency(pe.charge_cost_eur) : '—'}</td>
                    <td className="px-2 py-0.5 text-right font-mono">{fmtCurrency(pe.vom_cost_eur)}</td>
                    <td className="px-2 py-0.5 text-right font-mono">{fmtCurrency(pe.fixed_cost_eur)}</td>
                    <td className="px-2 py-0.5 text-right"><ProfitChip value={pe.net_profit_eur} /></td>
                    <td className="px-2 py-0.5 text-right font-mono">
                      {pe.lcoe_or_lcos != null ? `${pe.lcoe_or_lcos.toFixed(2)} €/MWh` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </td>
        </tr>
      )}
    </>
  )
}

// ── LCOH section ────────────────────────────────────────────────────────────
// Levelised Cost of Hydrogen, integrated into the Economics tab so it lives
// alongside per-asset LCOE/LCOS instead of in a separate Hydrogen tab. The
// section picks the period slice from `useResultsFilter()`:
//   • 'all' (Aggregated)            → fleet horizon LCOH + per-link horizon LCOH
//   • specific period (e.g. 2026)   → fleet period LCOH + per-link period LCOH
// When the user picks a period AND the network is multi-period AND the
// payload carries `by_period`, we substitute that period's values into the
// table. Backwards-compatible: single-period networks see only the horizon
// view (which collapses to the same numbers).

function LcohSection() {
  const filter = useResultsFilter()
  const { data: lcoh, isLoading } = useQuery({
    queryKey: ['results', 'lcoh'],
    queryFn: resultsApi.getLcoh,
  })
  if (isLoading) return null
  if (!lcoh || lcoh.rows.length === 0) return null

  const isPeriod = filter.selectedPeriod != null && typeof filter.selectedPeriod === 'number'
  const periodInt = isPeriod ? (filter.selectedPeriod as number) : null

  // Pick fleet KPI source: horizon `total` (default) or the matching
  // by_period entry. The `total` object can be null on networks with no
  // production this run; guard for it.
  const fleet = lcoh.total
  const fleetPeriod = isPeriod && fleet?.by_period
    ? fleet.by_period.find(p => p.period === periodInt) ?? null
    : null
  const usePeriodView = !!fleetPeriod

  const fleetH2 = usePeriodView ? fleetPeriod!.h2_produced_mwh : (fleet?.h2_produced_mwh ?? 0)
  const fleetCapex = usePeriodView ? fleetPeriod!.capex_eur : (fleet?.capex_eur_per_year ?? 0)
  const fleetVom = usePeriodView ? fleetPeriod!.vom_cost_eur : (fleet?.vom_cost_eur ?? 0)
  const fleetElec = usePeriodView ? fleetPeriod!.electricity_cost_eur : (fleet?.electricity_cost_eur ?? 0)
  const fleetLcohMwh = usePeriodView ? fleetPeriod!.lcoh_eur_per_mwh_h2 : (fleet?.lcoh_eur_per_mwh_h2 ?? null)
  const fleetLcohKg = usePeriodView ? fleetPeriod!.lcoh_eur_per_kg_h2 : (fleet?.lcoh_eur_per_kg_h2 ?? null)

  const scopeLabel = usePeriodView ? `period ${periodInt}` : 'horizon (all periods)'

  return (
    <section>
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">
          Levelised Cost of Hydrogen (LCOH)
          <span className="ml-2 text-[10px] font-normal text-muted">{scopeLabel}</span>
        </h3>
        <span className="text-[10px] text-muted">
          {lcoh.rows.length} electrolyser{lcoh.rows.length === 1 ? '' : 's'}
        </span>
      </div>
      <p className="text-[11px] text-muted mb-2.5">
        LCOH = (annuitised CAPEX + variable OPEX + electricity input cost) ÷ H₂ produced.
        €/kg uses LHV-based 33.33 kWh/kg conversion.
      </p>
      {fleet && (
        <div className="grid gap-3 grid-cols-2 md:grid-cols-4 mb-3">
          <KPI accent label="Fleet LCOH"
               value={fleetLcohMwh != null ? `${fleetLcohMwh.toFixed(1)} €/MWh` : '—'}
               sub={fleetLcohKg != null ? `${fleetLcohKg.toFixed(2)} €/kg` : ''}
               hint={`Σ (capex + opex + elec) ÷ Σ H₂ produced over ${scopeLabel}`} />
          <KPI label="H₂ produced"
               value={fmtEnergy(fleetH2)}
               sub={`across ${scopeLabel}`}
               hint="Σ p0_consume × efficiency × weights — output at bus1 in MWh_H2" />
          <KPI label="Annuitised CAPEX"
               value={fmtCurrency(fleetCapex)}
               sub="electrolyser fleet"
               hint="Σ capital_cost × p_nom_opt scaled by ipw.years for the selected scope" />
          <KPI label="Electricity input cost"
               value={fmtCurrency(fleetElec)}
               sub="bus0 marginal price × consume"
               hint="What the LP paid the grid for electrons fed into the electrolysers" />
        </div>
      )}
      <div className="rounded-[10px] border border-border bg-bg overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-[11.5px] min-w-[900px]">
            <thead className="bg-bg-2 border-b border-border">
              <tr className="text-muted">
                <th className="text-left  py-1 px-3 font-medium">Link</th>
                <th className="text-right py-1 px-3 font-medium">Carrier</th>
                <th className="text-right py-1 px-3 font-medium">p_nom (MW)</th>
                <th className="text-right py-1 px-3 font-medium">η</th>
                <th className="text-right py-1 px-3 font-medium">H₂ produced</th>
                <th className="text-right py-1 px-3 font-medium">CAPEX</th>
                <th className="text-right py-1 px-3 font-medium">VOM</th>
                <th className="text-right py-1 px-3 font-medium">Electricity</th>
                <th className="text-right py-1 px-3 font-medium">€/MWh_H₂</th>
                <th className="text-right py-1 px-3 font-medium">€/kg_H₂</th>
              </tr>
            </thead>
            <tbody>
              {lcoh.rows.map(r => {
                const periodRow = isPeriod && r.by_period
                  ? r.by_period.find(p => p.period === periodInt) ?? null
                  : null
                const useP = !!periodRow
                const h2 = useP ? periodRow!.h2_produced_mwh : r.h2_produced_mwh
                const cap = useP ? periodRow!.capex_eur : r.capex_eur_per_year
                const vom = useP ? periodRow!.vom_cost_eur : r.vom_cost_eur
                const elec = useP ? periodRow!.electricity_cost_eur : r.electricity_cost_eur
                const lcohMwh = useP ? periodRow!.lcoh_eur_per_mwh_h2 : r.lcoh_eur_per_mwh_h2
                const lcohKg = useP ? periodRow!.lcoh_eur_per_kg_h2 : r.lcoh_eur_per_kg_h2
                return (
                  <tr key={r.name} className="border-b border-border/40">
                    <td className="py-1 px-3 text-text font-mono">{r.name}</td>
                    <td className="py-1 px-3 text-right text-muted">{r.carrier || '—'}</td>
                    <td className="py-1 px-3 text-right font-mono text-text">{r.p_nom_opt_mw.toFixed(1)}</td>
                    <td className="py-1 px-3 text-right font-mono text-text">{r.efficiency.toFixed(2)}</td>
                    <td className="py-1 px-3 text-right font-mono text-text">{fmtEnergy(h2)}</td>
                    <td className="py-1 px-3 text-right font-mono text-muted">{fmtCurrency(cap)}</td>
                    <td className="py-1 px-3 text-right font-mono text-muted">{fmtCurrency(vom)}</td>
                    <td className="py-1 px-3 text-right font-mono text-muted">{fmtCurrency(elec)}</td>
                    <td className="py-1 px-3 text-right font-mono text-text">
                      {lcohMwh != null ? lcohMwh.toFixed(1) : '—'}
                    </td>
                    <td className="py-1 px-3 text-right font-mono text-text">
                      {lcohKg != null ? lcohKg.toFixed(2) : '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  )
}
