// Response contract for GET /api/results/asset/{class}/{name}.
// Mirrors services/asset_results/service.py::build_response. The frontend
// holds NO metric knowledge — labels, units, formulas and applicability all
// arrive from the backend registry.

export type MetricStatus = 'ok' | 'blocked' | 'na'
export type ViewMode = 'chronological' | 'duration' | 'monthly'

export interface Remedy {
  /** Closed set — see applicability.VALID_ACTIONS. */
  action: 'run_simulation' | 'run_ac_pf' | 'open_properties'
  label: string
}

export interface CategoryStatus {
  id: string
  label: string
  status: MetricStatus
  reason?: string
  remedy?: Remedy
}

export interface MetricRow {
  id: string
  label: string
  unit: string
  kind: 'series' | 'scalar'
  origin: 'output' | 'input' | 'derived'
  status: MetricStatus
  reason?: string
  remedy?: Remedy
  formula?: string
}

export interface ColumnSpec {
  id: string
  label: string
  unit: string
  metric_id: string
  agg: 'mean' | 'max' | 'energy' | null
}

export interface AssetRef {
  class: string
  name: string
  carrier: string
  bus: string
}

/**
 * A headline KPI on the Summary tab, lifted from one of the other
 * categories. `value` is present only when `status === 'ok'`; a blocked or
 * n/a headline carries its `reason` instead so the row still renders and
 * explains itself. Mirrors registry.HEADLINE + service.build_headline.
 */
export interface HeadlineRow {
  id: string
  label: string
  unit: string
  category: string
  category_label: string
  origin: 'output' | 'input' | 'derived'
  status: MetricStatus
  value?: number | string | null | Record<string, number | null>
  reason?: string
  remedy?: Remedy
  formula?: string
}

export interface AssetResultsResponse {
  asset: AssetRef & { params: Record<string, unknown> }
  solve: {
    source: 'lopf' | 'ac_pf'
    objective: number | null
    solve_time: number | null
    condition: string | null
  }
  category: string
  mode: ViewMode
  categories: CategoryStatus[]
  metrics: MetricRow[]
  scalars: Record<string, number | string | null | Record<string, number | null>>
  /** Populated only when `category === 'summary'`; `[]` on every other tab. */
  headline: HeadlineRow[]
  index: string[]
  periods: Array<number | string> | null
  pct_of_hours: number[] | null
  columns: ColumnSpec[]
  series: Record<string, Array<number | null>>
}

/** Display order of the category strip. Must match registry.CATEGORIES. */
export const CATEGORY_ORDER = [
  'summary', 'capacity', 'dispatch', 'storage',
  'loadflow', 'prices', 'economics', 'emissions',
] as const
