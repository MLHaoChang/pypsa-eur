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
