// The project's one reliability goal (UX-flow audit row 1, W3b-R): the allowed
// shortfall, the energy strictness (ENS target), the MC draws and the site
// type, read from `GET /projects/{name}/flow/reliability_goal` and written
// through the partial `PUT /simulation/solver_config`. Guided's Goal and Site
// cards, Expert's Solver settings and Grid connection, and every Results study
// read the same query, so both modes show the same stored goal.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getReliabilityGoal, type ReliabilityGoal } from '../api/flow'
import { simulationApi } from '../api/simulation'
import type { EhArchetype } from '../api/simulation'
import type { SolverConfig } from '../api/types'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'

/** The goal fields the editor edits (the site type has its own select). */
export type GoalNumberField = 'target_lole_h' | 'ens_cap_permyriad' | 'mc_draws'
/** The CO₂ limit goal (DC review 02): edited with the reliability goal and
 *  saved with it, but not part of `GET …/reliability_goal`; its stored value
 *  is the solver config's (`co2_limit_t_per_yr`). */
export const CO2_LIMIT = 'co2_limit_t_per_yr'
export type GoalCo2Field = typeof CO2_LIMIT
/** What the user typed, per field; a field absent here was not edited. */
export type GoalEdits = Partial<Record<GoalNumberField | GoalCo2Field, string>>
/** The fields a save sends: only the edited ones. */
export type GoalPatch = Partial<Pick<SolverConfig,
  'target_lole_h' | 'ens_cap_permyriad' | 'mc_draws' | 'site_archetype' | 'co2_limit_t_per_yr'>>

/** The CO₂ limit's words, the same in both modes (UX GO on DC 02 (1)). */
export const CO2_LIMIT_UNIT = 't CO₂/yr'
export const CO2_LIMIT_ERROR = `CO₂ limit must be 0 or more ${CO2_LIMIT_UNIT}.`

/** The engine's draw cap (`services/adequacy/mc.py` MAX_DRAWS). */
export const MAX_GOAL_DRAWS = 2000

export const reliabilityGoalKey = (project: string | null) => nk(project, 'reliabilityGoal')

/** The edited fields as a save body. Blank = not set (null): no shortfall
 *  target, no ENS target, the default draws. Never carries an unedited field;
 *  an invalid value returns an error naming it instead of a patch. */
export function goalPatchFromEdits(edits: GoalEdits): { patch: GoalPatch; error: string | null } {
  const patch: GoalPatch = {}
  const raw = (k: GoalNumberField | GoalCo2Field) => (edits[k] ?? '').trim()
  if ('target_lole_h' in edits) {
    const v = Number(raw('target_lole_h'))
    if (raw('target_lole_h') === '') patch.target_lole_h = null
    else if (!Number.isFinite(v) || v < 0) return { patch: {}, error: 'Allowed shortfall must be 0 or more hours per year.' }
    else patch.target_lole_h = v
  }
  if ('ens_cap_permyriad' in edits) {
    const v = Number(raw('ens_cap_permyriad'))
    // 0 = off, as the Solver settings field has always read it.
    if (raw('ens_cap_permyriad') === '' || v === 0) patch.ens_cap_permyriad = null
    else if (!Number.isFinite(v) || v < 0) return { patch: {}, error: 'ENS target must be more than 0 ‱ (0 = off).' }
    else patch.ens_cap_permyriad = v
  }
  if ('mc_draws' in edits) {
    const v = Number(raw('mc_draws'))
    if (raw('mc_draws') === '') patch.mc_draws = null
    else if (!Number.isInteger(v) || v < 1 || v > MAX_GOAL_DRAWS) {
      return { patch: {}, error: `MC draws must be a whole number from 1 to ${MAX_GOAL_DRAWS}.` }
    } else patch.mc_draws = v
  }
  if (CO2_LIMIT in edits) {
    // Blank = no limit (null); 0 is a limit: no emissions at all.
    const v = Number(raw(CO2_LIMIT))
    if (raw(CO2_LIMIT) === '') patch.co2_limit_t_per_yr = null
    else if (!Number.isFinite(v) || v < 0) return { patch: {}, error: CO2_LIMIT_ERROR }
    else patch.co2_limit_t_per_yr = v
  }
  return { patch, error: null }
}

/** A goal number as the editor shows it: the stored or template value, ''
 *  for a default (the placeholder names it). */
export function goalText(goal: ReliabilityGoal | null, k: GoalNumberField): string {
  const f = goal?.[k]
  if (!f || f.source === 'default' || f.value == null) return ''
  return String(f.value)
}

/** The goal's name where a run or a readiness row asks for it: the run
 *  profile's intake field and the template ledgers' `lole_goal` row use the
 *  same words (parity test in EnvelopeSection.test.tsx). */
export const RELIABILITY_GOAL_LABEL = 'Reliability goal (LOLE)'

/** The words after a goal nobody set, the site type's own (walkthrough 01;
 *  the Goal field's key line, template-e2e PR-C, says the same). */
export const ASSUMED_GOAL_NOTE = 'assumed for the site type'

/** The AssumedChip's source words for that goal (UX 2: the shared chip, one
 *  design with the Goal card's): "[Assumed] site type default". */
export const ASSUMED_GOAL_SOURCE = 'site type default'

/** The site type's assumed LOLE goal (h/yr), or null: a missing or
 *  non-number `assumed` reads as not set (GK B 65 C9). */
export function assumedLole(goal: ReliabilityGoal | null): number | null {
  const a: unknown = goal?.target_lole_h?.assumed
  return typeof a === 'number' && Number.isFinite(a) ? a : null
}

/** What an unset field reads, in both modes and in the Results summary. */
export const GOAL_UNSET: Record<'target_lole_h' | 'ens_cap_permyriad', string> = {
  target_lole_h: 'not set', ens_cap_permyriad: 'off',
}

/** A goal field in words, exactly as `GET …/reliability_goal` returns it:
 *  `3 h/yr`, `0.5‱`, `500`, or `not set` / `off`; a value that is not the
 *  user's own says where it came from (`(template)`, `(default)`). The one
 *  formatter the editor's placeholders (both modes) and the summary use. */
export function goalWords(goal: ReliabilityGoal | null, k: GoalNumberField): string {
  const f = goal?.[k]
  // An unset LOLE names the site type's assumed goal (walkthrough 01).
  const assumed = k === 'target_lole_h' && f?.value == null ? assumedLole(goal) : null
  if (assumed != null) return `${assumed} h/yr (${ASSUMED_GOAL_NOTE})`
  if (!f || f.value == null) return k === 'mc_draws' ? '' : GOAL_UNSET[k]
  const unit = k === 'target_lole_h' ? ' h/yr' : k === 'ens_cap_permyriad' ? '‱' : ''
  const note = f.source === 'template' ? ' (template)' : f.source === 'default' ? ' (default)' : ''
  return `${f.value}${unit}${note}`
}

export function useReliabilityGoal() {
  const project = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const key = reliabilityGoalKey(project)
  const q = useQuery({
    queryKey: key,
    queryFn: () => getReliabilityGoal(project as string),
    enabled: !!project,
    retry: false,
  })
  const goal = q.data ?? null
  /** Writes ONLY the fields in `patch` (the edited ones). */
  const save = useMutation({
    mutationFn: (patch: GoalPatch) => simulationApi.updateSolverConfig(patch),
    onSuccess: () => invalidateReliabilityGoal(qc, project),
  })
  return {
    project, goal, isPending: q.isPending && !!project, isError: q.isError, refetch: q.refetch,
    /** The site type every study runs (stored → template → strong_grid). */
    archetype: (goal?.site_archetype.value ?? 'strong_grid') as EhArchetype,
    /** The allowed shortfall in h/yr, or null when none is set. */
    target: goal?.target_lole_h.value ?? null,
    save,
  }
}

/** After any write of the goal (the Goal card, Solver settings' Save). */
export function invalidateReliabilityGoal(qc: ReturnType<typeof useQueryClient>, project: string | null) {
  void qc.invalidateQueries({ queryKey: reliabilityGoalKey(project) })
  void qc.invalidateQueries({ queryKey: nk(project, 'solverConfig') })
  void qc.invalidateQueries({ queryKey: nk(project, 'flow') })
}
