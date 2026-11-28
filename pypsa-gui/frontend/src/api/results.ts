// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

// "Why this number" for the Guided Energy Hub study's figures (guided-results 02a route, 02b disclosure):
// GET /results/eh_study/why/{figure}, a provenance walk from the figure to the study run and each input it rests
// on, each pinned recorded-vs-current. Reads only.
import { client } from './client'

export type EhStudyWhyFigure = 'expected_shortfall' | 'design_cost' | 'verdict'

/** fresh: every input compared equal; stale: one changed; uncompared: one could not be compared. */
export type WhyState = 'fresh' | 'stale' | 'uncompared'

export interface WhyRecorded {
  label?: string
  recorded?: unknown
  /** Why the study's own value cannot be compared (not recorded, wrong type, busy). */
  unrecorded?: string
}

export interface WhyStep {
  node: string
  kind: string
  label: string
  value: unknown
  unit: string | null
  state: WhyState
  stamp: Record<string, unknown> | null
  stamp_missing: string[]
  via: { edge: string; from: string; recorded: WhyRecorded | null } | null
  broken: {
    reason: string
    compared: { field?: string | null; recorded?: unknown; current?: unknown; current_field?: string | null }
    uncompared: string[]
  } | null
}

export interface EhStudyWhy {
  target: { study: 'eh_study'; figure: EhStudyWhyFigure }
  node: string
  outcome: { state: WhyState; uncompared: string[]; complete: boolean; truncated: boolean; steps_total: number }
  steps: WhyStep[]
}

/** Read for the open project: no `project_id`, which the route compares with the open project's UUID (the
 *  frontend knows the project by name only; a name there is refused 409 `project_not_open`). */
export const getEhStudyWhy = (figure: EhStudyWhyFigure): Promise<EhStudyWhy> =>
  client.get(`/results/eh_study/why/${figure}`, { skipErrorToast: true })
    .then(r => r.data as EhStudyWhy)

/** `GET /results/objective_decomposition`: why the LP objective and the reported total system cost differ
 *  (template-e2e 18). `lp_total = cost_breakdown_total − nonextendable_fixed_cost_eur +
 *  period_weighting_adjustment_eur − reserve_revenue_eur + residual_gap_eur`; the bridge fields are null under
 *  myopic foresight and rolling horizon (no single LP total), and every field may be null. */
export interface ObjectiveDecomposition {
  n_objective: number | null
  n_objective_constant: number | null
  lp_total: number | null
  cost_breakdown_total: number | null
  gap_eur: number | null
  lp_total_scope?: 'horizon' | 'last_period' | 'last_window'
  nonextendable_fixed_cost_eur: number | null
  period_weighting_adjustment_eur: number | null
  reserve_revenue_eur?: number
  residual_gap_eur: number | null
  /** The auto period discount the solve applied (its stamp); null when it applied none, "unknown" for a
   *  result solved before the stamp. */
  discount: { ref_year: number; real_rate: number } | null | 'unknown'
}

/** Null when nothing is solved (204). */
export const getObjectiveDecomposition = (): Promise<ObjectiveDecomposition | null> =>
  client.get<ObjectiveDecomposition>('/results/objective_decomposition', { skipErrorToast: true })
    .then(r => (r.status === 204 ? null : r.data))
