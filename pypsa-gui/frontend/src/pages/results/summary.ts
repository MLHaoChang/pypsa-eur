// M3 (plan 2026-10-14-m3-results-summary.md): the Summary tab's arithmetic,
// pure so it is tested once. Every figure is a field an existing endpoint
// returns, or one named subtraction of two; nothing here re-derives what the
// engine computes. Each view takes the shared period selector (null = the
// horizon totals the endpoints already sum).
import type { CostBreakdown } from '../../api/simulation'

type Period = number | string | null

const samePeriod = (a: number | string, b: number | string) => a === b || String(a) === String(b)

export interface CostView {
  total: number
  capital: number          // annualised capital cost, fixed O&M included (`capex`)
  operating: number        // `opex`
  /** Annualised capital of capacity built by this solve (`capex_expansion`); horizon only. */
  newBuild: number | null
  period: number | string | null
}

/** The cost card: `cost_breakdown`'s horizon fields, or the selected period's `by_period` row. */
export function costView(cost: CostBreakdown | null | undefined, period: Period): CostView | null {
  if (!cost) return null
  const row = period != null ? cost.by_period.find(p => samePeriod(p.period, period)) : undefined
  if (row) return { total: row.total, capital: row.capex, operating: row.opex, newBuild: null, period: row.period }
  return { total: cost.total, capital: cost.capex, operating: cost.opex, newBuild: cost.capex_expansion, period: null }
}

export interface EmissionsPayload {
  total_tCO2: number
  caps: Array<{
    name: string; investment_period: number | null; scope: 'period' | 'horizon'
    cap_tCO2: number | null; used_tCO2: number; shadow_price_eur_per_tCO2: number; binding: boolean
  }>
  by_period: Array<{ period: number | string; total_tCO2: number }>
}

export interface EmissionsView {
  total_tCO2: number
  caps: EmissionsPayload['caps']
  period: number | string | null
}

/** The emissions card: the total for the scope, and the CO₂ caps that apply to it. */
export function emissionsView(e: EmissionsPayload | null | undefined, period: Period): EmissionsView | null {
  if (!e) return null
  const row = period != null ? e.by_period.find(p => samePeriod(p.period, period)) : undefined
  const caps = (e.caps ?? []).filter(c => c.scope === 'horizon'
    || (row ? c.investment_period != null && samePeriod(c.investment_period, row.period) : true))
  return row
    ? { total_tCO2: row.total_tCO2, caps, period: row.period }
    : { total_tCO2: e.total_tCO2, caps, period: null }
}

export interface CapacityRow {
  component: string
  carrier: string
  unit: 'MW' | 'MWh'
  capacity: number         // PyPSA's Optimal Capacity
  /** Built by this solve: max(0, Optimal − Installed Capacity), the rule `capex_expansion` uses. */
  newBuild: number
}

// The classes whose capacity is a size a reader means by "what got built": power for generators and
// storage units, energy for stores. Branches and loads are left to their own tabs.
const CAPACITY_UNIT: Record<string, 'MW' | 'MWh'> = { Generator: 'MW', StorageUnit: 'MW', Store: 'MWh' }

type StatsRow = Record<string, unknown>

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

/**
 * The capacity card from `/results/statistics` (PyPSA `n.statistics()`): one row per (component, carrier), largest
 * first. The index columns arrive unnamed (`level_0`, `level_1`) from PyPSA 1.1. A multi-period table has
 * "<metric> | <period>" columns: the selected period's, or the last period's (the fleet at the end of the
 * horizon) when none is selected.
 */
export function capacityRows(stats: StatsRow[] | null | undefined, period: Period): CapacityRow[] {
  if (!stats?.length) return []
  const keys = Object.keys(stats[0])
  const periods = [...new Set(keys.filter(k => k.startsWith('Optimal Capacity | '))
    .map(k => k.slice('Optimal Capacity | '.length)))]
  const pick = periods.length
    ? (period != null && periods.some(p => samePeriod(p, period)) ? String(period) : periods[periods.length - 1])
    : null
  const col = (metric: string) => (pick ? `${metric} | ${pick}` : metric)
  const out: CapacityRow[] = []
  for (const r of stats) {
    const component = String(r.component ?? r.level_0 ?? '')
    const carrier = String(r.carrier ?? r.level_1 ?? '')
    const unit = CAPACITY_UNIT[component]
    const capacity = num(r[col('Optimal Capacity')])
    if (!unit || capacity == null || capacity <= 0) continue
    const installed = num(r[col('Installed Capacity')]) ?? 0
    out.push({ component, carrier, unit, capacity, newBuild: Math.max(0, capacity - installed) })
  }
  return out.sort((a, b) => (a.unit === b.unit ? b.capacity - a.capacity : a.unit === 'MW' ? -1 : 1))
}

export interface LostLoadPayload {
  total_mwh: number
  total_cost_eur: number
  voll_eur_per_mwh: number
  shed_hours?: { total: number; by_period: Record<string, number> }
}

export interface ReserveMarginSummary {
  margin: number
  by_period: Array<{ period: number | string; met: boolean; binding: boolean }>
}

/** "met in every period" / "not met in 1 of 3 periods" for the reserve margin the solve enforced. */
export function reserveMarginVerdict(rm: ReserveMarginSummary | null | undefined): { met: boolean; text: string } | null {
  if (!rm || !rm.by_period?.length) return null
  const missed = rm.by_period.filter(p => !p.met).length
  const n = rm.by_period.length
  if (!missed) return { met: true, text: n === 1 ? 'met' : `met in all ${n} periods` }
  return { met: false, text: n === 1 ? 'not met' : `not met in ${missed} of ${n} periods` }
}
