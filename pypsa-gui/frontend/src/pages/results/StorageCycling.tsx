import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
} from 'recharts'
import { resultsApi } from '../../api/simulation'
import { networkApi } from '../../api/network'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import type { StorageUnit } from '../../api/types'
import {
  type TSPayload,
  CHART_GRID, CHART_AXIS, CHART_TOOLTIP, yAxisLabel,
} from './shared'
import { resolveRange, useResultsFilter } from './filterContext'
import { useResultsWindow } from '../../hooks/useResultsWindow'
import { useFilterableTable, TableSearchBox, SortHeader } from './useFilterableTable'

// ── Storage cycling result tab ──────────────────────────────────────────────
// Per-storage-unit cycle count (Σ |p| × Δt ÷ (2 × p_nom_opt × max_hours))
// computed directly from `storage_units_t.p` so the result is correct
// regardless of whether `vintage_results` is populated. Aggregates per
// carrier (battery / hydrogen / heat / pumped hydro) for the headline view
// and offers a per-unit drill-down. Respects the period filter — when a
// specific period is selected, the throughput is restricted to that period's
// snapshots.

const STORAGE_BATTERY_ALIASES  = new Set(['battery', 'bess', 'li-ion', 'lithium', 'lithium-ion'])
const STORAGE_HYDROGEN_ALIASES = new Set(['h2', 'hydrogen', 'h2 storage', 'h2_storage', 'hydrogen storage'])
const STORAGE_HEAT_ALIASES     = new Set(['heat', 'thermal', 'tes', 'thermal storage', 'heat storage'])
const STORAGE_PHS_ALIASES      = new Set(['phs', 'pumped hydro', 'pumped storage', 'psh'])
function storageCarrierKey(raw: string | undefined | null): string {
  const c = (raw ?? '').trim().toLowerCase()
  if (STORAGE_BATTERY_ALIASES.has(c))  return 'battery'
  if (STORAGE_HYDROGEN_ALIASES.has(c)) return 'hydrogen'
  if (STORAGE_HEAT_ALIASES.has(c))     return 'heat'
  if (STORAGE_PHS_ALIASES.has(c))      return 'pumped_hydro'
  return c || 'unspecified'
}
function storageCarrierLabel(k: string): string {
  if (k === 'battery')      return 'Battery'
  if (k === 'hydrogen')     return 'Hydrogen'
  if (k === 'heat')         return 'Heat'
  if (k === 'pumped_hydro') return 'Pumped Hydro'
  if (k === 'unspecified')  return 'Unspecified'
  return k.charAt(0).toUpperCase() + k.slice(1)
}

interface UnitRow {
  name: string
  carrier: string
  pNom: number
  energyCap: number
  throughput: number
  cycles: number
}

// Per-period cumulative capacity for a vintage-expanded asset:
// initial + Σ vintages with build_year ≤ period. Returns null when the asset
// has no vintage entry (the caller should fall back to the parent's final
// p_nom_opt × max_hours in that case).
interface VintageResultsShape {
  results: Record<string, Record<string, {
    capacity_field: string
    initial_capacity: number
    periods: Array<{ build_year: number; p_nom_opt: number }>
  }>>
}
function vintageCapAtPeriod(
  vr: VintageResultsShape | null | undefined,
  componentClass: 'StorageUnit',
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

export default function StorageCycling() {
  const currentProject = useUIStore(s => s.currentProject)
  const filter = useResultsFilter()
  // Positional bounds for the active Horizon filter, resolved against the
  // SNAPSHOT INDEX (not a results payload) — same hook Dispatch uses so the
  // storage-dispatch query below can be windowed before any payload exists.
  const { win, winValid } = useResultsWindow(currentProject)
  const { data: storPowerTS } = useQuery({
    queryKey: nk(currentProject, 'results', 'storage_dispatch', win.from, win.to),
    queryFn: () => resultsApi.getStorageDispatchResults(undefined, win),
    enabled: winValid,
  })
  const { data: storageUnits = [] } = useQuery({ queryKey: nk(currentProject, 'storage_units'), queryFn: networkApi.getStorageUnits })
  const { data: snap } = useQuery({ queryKey: nk(currentProject, 'snapshots'), queryFn: networkApi.getSnapshots })
  const { data: vintageResults } = useQuery({ queryKey: nk(currentProject, 'vintage_results'), queryFn: networkApi.listVintageResults })

  // Resolve the row range using the same snapshot timeline the dispatch
  // chart uses. Defaults to the full series when no horizon filter / period
  // is active.
  const refIndex: string[] = (storPowerTS as TSPayload | null)?.index ?? snap?.snapshots ?? []
  const refPeriods = (storPowerTS as TSPayload | null)?.periods ?? snap?.periods
  const range = resolveRange(refIndex, filter, refPeriods)

  // Per-unit accumulator. Walks the storage-power TS once and aggregates
  // |p| × snap_weight per (asset, period). Per-period capacity comes from
  // vintage_results (initial + Σ vintages with build_year ≤ P) so a battery
  // that was 50 MW in 2026 and grew to 472 MW in 2028 reads the correct
  // 200 MWh denominator for 2026 instead of the final 1888 MWh.
  //
  // Without this, period-filtered cycles for early years collapse to
  // throughput / (2 × FINAL_cap) and a battery running near daily cycling
  // (~365 cycles/year) on its early 50 MW shows as ~42 — the user-reported
  // bug. See projects.py:_per_period_pnom for the matching backend logic.
  const units: UnitRow[] = useMemo(() => {
    const ts = storPowerTS as TSPayload | null
    if (!ts || ts.data.length === 0) return []
    const meta = new Map<string, { pNom: number; maxHours: number; carrier: string }>()
    for (const s of storageUnits as StorageUnit[]) {
      const pNom = Number((s as unknown as { p_nom_opt?: number }).p_nom_opt
                  ?? (s as unknown as { p_nom?: number }).p_nom
                  ?? 0)
      const maxHours = Number((s as unknown as { max_hours?: number }).max_hours ?? 0)
      meta.set(s.name, { pNom, maxHours, carrier: storageCarrierKey(s.carrier) })
    }
    // Keyed by snapshot ISO (flat) or `period|timestep` (multi-period) rather
    // than array position. `snap.weightings` always covers the FULL horizon,
    // but `storPowerTS` — and therefore `row` below — is now a WINDOWED
    // slice: row 0 is whichever absolute snapshot the window starts at, not
    // absolute row 0. A positional `weight[row]` lookup silently pulled the
    // wrong snapshot's weight whenever the window didn't start at the
    // horizon's first row. Matching by the row's own timestamp/period is
    // correct regardless of the window's offset — same approach `shared.tsx`
    // uses for `weightedSum`/`weightedSumSplit`.
    const weightByKey = new Map<string, number>()
    for (const r of (snap?.weightings as unknown as Array<Record<string, unknown>> | undefined) ?? []) {
      const w = r.objective
      if (typeof w !== 'number' || !Number.isFinite(w)) continue
      const snapshotIso = r.snapshot as string | undefined
      const timestep = r.timestep as string | undefined
      const period = r.period as number | string | undefined
      if (snapshotIso != null) weightByKey.set(snapshotIso, w)
      else if (timestep != null) weightByKey.set(period != null ? `${period}|${timestep}` : timestep, w)
    }
    const wAt = (iso: string, period: number | null): number => {
      if (weightByKey.size === 0) return 1
      const w = (period != null ? weightByKey.get(`${period}|${iso}`) : undefined) ?? weightByKey.get(iso)
      return typeof w === 'number' && Number.isFinite(w) ? w : 1
    }

    const lastRow = ts.data.length - 1
    const rFrom = Math.max(0, Math.min(range.from, lastRow))
    const rTo   = Math.max(0, Math.min(range.to,   lastRow))
    // Bucket throughput by (column, period). For flat networks the period
    // key is `null` and there's only one bucket per column.
    const periods = ts.periods ?? snap?.periods ?? null
    const throughputByColPeriod = new Map<string, Map<number | null, number>>()
    const periodSet = new Set<number | null>()
    for (let row = rFrom; row <= rTo; row++) {
      const raw = periods ? periods[row] : undefined
      const p: number | null = typeof raw === 'number' ? raw
                              : typeof raw === 'string' && /^-?\d+$/.test(raw) ? Number(raw)
                              : null
      const w = wAt(ts.index[row], p)
      periodSet.add(p)
      for (let col = 0; col < ts.columns.length; col++) {
        const v = ts.data[row][col]
        if (!Number.isFinite(v)) continue
        const key = ts.columns[col]
        let perPeriod = throughputByColPeriod.get(key)
        if (!perPeriod) { perPeriod = new Map(); throughputByColPeriod.set(key, perPeriod) }
        perPeriod.set(p, (perPeriod.get(p) ?? 0) + Math.abs(v) * w)
      }
    }

    const selectedPeriod = filter.selectedPeriod
    const out: UnitRow[] = []
    for (const [name, perPeriod] of throughputByColPeriod) {
      const m = meta.get(name)
      if (!m) continue
      const finalEnergyCap = m.pNom * m.maxHours

      // capForPeriod resolves the cumulative-by-period capacity. Falls back
      // to finalEnergyCap when no vintage record exists (non-extendable
      // asset, or vintage_results not populated).
      const capForPeriod = (p: number | null): number => {
        if (p == null || !vintageResults) return finalEnergyCap
        const v = vintageCapAtPeriod(vintageResults, 'StorageUnit', name, p)
        if (v == null || !Number.isFinite(v) || v <= 0) return finalEnergyCap
        return v * m.maxHours
      }

      if (selectedPeriod != null && typeof selectedPeriod === 'number') {
        // Period filter active — single-period values.
        const tp = perPeriod.get(selectedPeriod) ?? 0
        const ec = capForPeriod(selectedPeriod)
        const pNomP = ec > 0 ? ec / m.maxHours : m.pNom
        out.push({
          name,
          carrier: m.carrier,
          pNom: pNomP,
          energyCap: ec,
          throughput: tp,
          cycles: ec > 0 ? tp / (2 * ec) : 0,
        })
        continue
      }

      // Aggregated view — average per-period cycles (matches the backend
      // _compute_storage_cycling_summary convention so a 3-year horizon
      // doesn't read 3× the per-year count).
      let sumCycles = 0
      let sumTp = 0
      let sumEc = 0
      let nActive = 0
      for (const p of periodSet) {
        const tp = perPeriod.get(p) ?? 0
        const ec = capForPeriod(p)
        if (ec <= 0) continue
        sumCycles += tp / (2 * ec)
        sumTp += tp
        sumEc += ec
        nActive += 1
      }
      if (nActive === 0) {
        out.push({ name, carrier: m.carrier, pNom: m.pNom, energyCap: finalEnergyCap, throughput: 0, cycles: 0 })
        continue
      }
      out.push({
        name,
        carrier: m.carrier,
        pNom: (sumEc / nActive) / (m.maxHours || 1),
        energyCap: sumEc / nActive,
        throughput: sumTp / nActive,
        cycles: sumCycles / nActive,
      })
    }
    out.sort((a, b) => b.cycles - a.cycles)
    return out
  }, [storPowerTS, storageUnits, snap, range, vintageResults, filter.selectedPeriod])

  // Aggregate per-carrier cycle counts: weighted average of unit cycles
  // by energy capacity, so a large unit doesn't get the same weight as a
  // tiny one.
  const carrierStats = useMemo(() => {
    const groups = new Map<string, { tp: number; ec: number }>()
    for (const u of units) {
      const g = groups.get(u.carrier) ?? { tp: 0, ec: 0 }
      g.tp += u.throughput
      g.ec += u.energyCap
      groups.set(u.carrier, g)
    }
    return Array.from(groups.entries())
      .map(([carrier, g]) => ({
        carrier,
        cycles: g.ec > 0 ? g.tp / (2 * g.ec) : 0,
        throughput: g.tp,
        energyCap: g.ec,
      }))
      .sort((a, b) => b.cycles - a.cycles)
  }, [units])

  if (!storPowerTS || units.length === 0) {
    return (
      <div className="p-6 text-[12px] text-muted">
        No storage cycling data available. Run a LOPF with at least one extendable storage unit
        to populate <code className="font-mono">storage_units_t.p</code>.
      </div>
    )
  }

  const periodLabel = filter.selectedPeriod != null ? `period ${filter.selectedPeriod}` : 'horizon'

  return (
    <div className="flex flex-col h-full overflow-auto p-4 gap-4">
      <header>
        <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">
          Storage cycling
          <span className="ml-2 text-[10px] font-normal text-muted">{periodLabel}</span>
        </h3>
        <p className="text-[11px] text-muted mt-1">
          Equivalent full cycles per storage unit. Cycle = Σ |p| × Δt ÷ (2 × p_nom_opt × max_hours)
          — counts charge AND discharge throughput so a battery doing one full discharge + one
          full recharge reads as exactly 1 cycle.
        </p>
      </header>

      {/* Carrier-level cycle counts as a bar chart */}
      <section className="rounded-[10px] border border-border bg-bg overflow-hidden">
        <header className="px-4 py-2.5 border-b border-border bg-bg-2">
          <span className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">By carrier</span>
          <span className="ml-2 text-[10px] text-muted">capacity-weighted</span>
        </header>
        <div className="p-3">
          <ResponsiveContainer width="100%" height={220}>
            <BarChart
              data={carrierStats.map(c => ({ ...c, label: storageCarrierLabel(c.carrier) }))}
              margin={{ top: 4, right: 12, left: 0, bottom: 4 }}
            >
              <CartesianGrid {...CHART_GRID} />
              <XAxis dataKey="label" {...CHART_AXIS} />
              <YAxis {...CHART_AXIS} label={yAxisLabel('Cycles')} />
              <Tooltip {...CHART_TOOLTIP}
                formatter={(v: number) => [`${v.toFixed(2)} cycles`, 'cycles']} />
              <Bar dataKey="cycles" fill="#7c3aed" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </section>

      <PerUnitCyclingSection units={units} />

    </div>
  )
}

function PerUnitCyclingSection({ units }: { units: UnitRow[] }) {
  type K = 'name' | 'carrier' | 'pNom' | 'energyCap' | 'throughput' | 'cycles'
  const ft = useFilterableTable<UnitRow, K>({
    rows: units,
    initialSortKey: 'cycles',
    initialSortDir: 'desc',
    getSearchText: r => [r.name, r.carrier].join(' ').toLowerCase(),
    getValue: (r, k) => (k === 'name' || k === 'carrier') ? r[k] : r[k] as number,
  })
  return (
    <section className="rounded-[10px] border border-border bg-bg overflow-hidden">
      <header className="px-4 py-2.5 border-b border-border bg-bg-2 flex items-center gap-2 flex-wrap">
        <span className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">Per-unit detail</span>
        <span className="text-[10px] text-muted">click headers to sort</span>
        <span className="flex-1" />
        <TableSearchBox value={ft.search} onChange={ft.setSearch} placeholder="Filter…" />
      </header>
      <div className="p-3">
        <table className="w-full text-[11.5px]">
          <thead>
            <tr className="border-b border-border text-muted">
              <SortHeader columnKey="name"       label="Unit"               sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('name')} />
              <SortHeader columnKey="carrier"    label="Carrier"            sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('carrier')}    align="right" />
              <SortHeader columnKey="pNom"       label="p_nom (MW)"         sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('pNom')}       align="right" />
              <SortHeader columnKey="energyCap"  label="Energy (MWh)"       sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('energyCap')}  align="right" />
              <SortHeader columnKey="throughput" label="Throughput (MWh)"   sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('throughput')} align="right" />
              <SortHeader columnKey="cycles"     label="Cycles"             sortKey={ft.sortKey} sortDir={ft.sortDir} onClick={ft.onSortClick('cycles')}     align="right" />
            </tr>
          </thead>
          <tbody>
            {ft.rows.map(u => (
              <tr key={u.name} className="border-b border-border/40">
                <td className="py-1 text-text font-mono">{u.name}</td>
                <td className="py-1 text-right text-muted">{storageCarrierLabel(u.carrier)}</td>
                <td className="py-1 text-right font-mono text-text">{u.pNom.toFixed(1)}</td>
                <td className="py-1 text-right font-mono text-text">{u.energyCap.toFixed(1)}</td>
                <td className="py-1 text-right font-mono text-muted">{u.throughput.toFixed(0)}</td>
                <td className="py-1 text-right font-mono text-text">{u.cycles.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
