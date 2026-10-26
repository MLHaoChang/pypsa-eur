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
/** What the user typed, per field; a field absent here was not edited. */
export type GoalEdits = Partial<Record<GoalNumberField, string>>
/** The fields a save sends: only the edited ones. */
export type GoalPatch = Partial<Pick<SolverConfig,
  'target_lole_h' | 'ens_cap_permyriad' | 'mc_draws' | 'site_archetype'>>

/** The engine's draw cap (`services/adequacy/mc.py` MAX_DRAWS). */
export const MAX_GOAL_DRAWS = 2000

export const reliabilityGoalKey = (project: string | null) => nk(project, 'reliabilityGoal')

/** The edited fields as a save body. Blank = not set (null): no shortfall
 *  target, no ENS target, the default draws. Never carries an unedited field;
 *  an invalid value returns an error naming it instead of a patch. */
export function goalPatchFromEdits(edits: GoalEdits): { patch: GoalPatch; error: string | null } {
  const patch: GoalPatch = {}
  const raw = (k: GoalNumberField) => (edits[k] ?? '').trim()
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
  return { patch, error: null }
}

/** A goal number as the editor shows it: the stored or template value, ''
 *  for a default (the placeholder names it). */
export function goalText(goal: ReliabilityGoal | null, k: GoalNumberField): string {
  const f = goal?.[k]
  if (!f || f.source === 'default' || f.value == null) return ''
  return String(f.value)
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
