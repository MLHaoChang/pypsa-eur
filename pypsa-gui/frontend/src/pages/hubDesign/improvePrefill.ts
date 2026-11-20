// Improve card → Expert, by hand (template-e2e 22, owner decision
// IMPROVE-MANUAL-1): each finding opens the Expert place that makes the same
// change, with the change filled in and nothing applied. The user reviews
// it there and runs or saves it.
//
// A finding's action is `run_eh_study` (a study re-run) or
// `update_solver_config` (VOLL); the review emits no other (eh_review.py,
// fixture `__fixtures__/ehReviewActions.json`). A re-run's goal fields (ENS
// target, allowed shortfall, MC draws) are the project's one stored goal
// (W3b-R), edited in Solver settings; its other fields fill the study form in
// Results › Adequacy. A field neither place can show is named, never dropped.
import { create } from 'zustand'
import type { EhArchetype, EhReviewFinding } from '../../api/simulation'
import type { GoalEdits } from '../../hooks/useReliabilityGoal'
import {
  EMPTY_PACK_FORM, OPTIONAL_STAGES, leverKeysFor, type LeverKey, type PackForm,
} from '../results/EhReferenceDesignPanel'
import { useUIStore } from '../../store/uiStore'
import { openSectionInGuided, withReturnFrom } from '../../layout/flowNavigation'
import { useHubDesignStore } from './hubDesignStore'

/** Where "Open in Expert" goes. */
export type ImproveDest = 'study' | 'goal' | 'voll' | 'bounds'

export const DEST_WHERE: Record<ImproveDest, string> = {
  study: 'Results › Adequacy',
  goal: 'Solver settings › Reliability goal',
  voll: 'Solver settings › Value of lost load',
  bounds: 'Capacity bounds',
}

export interface ImprovePlan {
  dest: ImproveDest
  /** The study form, filled in (a re-run only). */
  pack?: PackForm
  /** The goal fields to fill in Solver settings (a re-run that changes the goal). */
  goal?: GoalEdits
  /** VOLL to fill in, €/MWh (the action's own unit). */
  voll?: number
  /** Plain words for each argument neither place can show. */
  unshown: string[]
}

type Rec = Record<string, unknown>
const isObj = (v: unknown): v is Rec => typeof v === 'object' && v !== null && !Array.isArray(v)
const str = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? String(v) : '')

/** Every request field the study form or the goal shows; the parity test
 *  checks this against the engine's `EhStudyRequest` schema. */
export const SHOWN_FIELDS = {
  request: ['archetype', 'budget_solves', 'dsr_buses', 'dtc_attribution', 'mc', 'pack_overrides', 'stages'],
  pack_overrides: ['certification_metric', 'ens_cap_permyriad', 'import_energy_mwh_per_year',
    'import_p_nom_mw', 'levers', 'target_lole_h'],
  mc: ['draws', 'seed'],
  levers: ['import_cap', 'import_energy', 'redundancy', 'storage_duration'],
} as const

/** Plain words for the fields neither place shows (the card names them). */
export const UNSHOWN_WORDS: Record<string, string> = {
  dtc_config: 'the islanding stress settings',
  'pack_overrides.mc_certify_required': 'whether certification is required',
  'pack_overrides.frontier_default': 'whether the frontier runs by default',
  'pack_overrides.dtc_stress_default': 'whether the islanding stress runs by default',
  'pack_overrides.dtc_planning_default': 'whether islanding sizing runs by default',
  'mc.cov_target': 'the sampling accuracy target',
}

const unshownWord = (path: string) => UNSHOWN_WORDS[path] ?? 'a setting this screen does not show'

/** The study re-run's arguments as the study form and the goal fields. */
export function planFromRerun(args: Rec, archetype: EhArchetype): ImprovePlan {
  const unshown: string[] = []
  const note = (path: string) => { const w = unshownWord(path); if (!unshown.includes(w)) unshown.push(w) }
  const po = isObj(args.pack_overrides) ? args.pack_overrides : {}
  const mc = isObj(args.mc) ? args.mc : {}
  for (const k of Object.keys(args)) {
    if (!(SHOWN_FIELDS.request as readonly string[]).includes(k)) note(k)
  }
  for (const k of Object.keys(po)) {
    if (!(SHOWN_FIELDS.pack_overrides as readonly string[]).includes(k)) note(`pack_overrides.${k}`)
  }
  for (const k of Object.keys(mc)) {
    if (!(SHOWN_FIELDS.mc as readonly string[]).includes(k)) note(`mc.${k}`)
  }
  // The study runs the stored goal's site type; another one cannot be shown.
  if (typeof args.archetype === 'string' && args.archetype !== archetype) {
    unshown.push('a different site type')
  }
  // `mc_lole` is what a LOLE target implies (the form sets it with the
  // target); `none` beside no target cannot be shown.
  if (po.certification_metric !== undefined
    && !(po.certification_metric === 'mc_lole' && typeof po.target_lole_h === 'number')) {
    unshown.push('the certification method')
  }
  const weak = archetype === 'weak_flexible'
  if (!weak && po.import_p_nom_mw !== undefined) unshown.push('an import limit')
  if (!weak && po.import_energy_mwh_per_year !== undefined) unshown.push('an import energy budget')
  if (!weak && Array.isArray(args.dsr_buses) && args.dsr_buses.length > 0) unshown.push('the flexible-demand buses')

  const levers: PackForm['levers'] = {}
  const offered = leverKeysFor(archetype)
  if (isObj(po.levers)) {
    for (const [k, v] of Object.entries(po.levers)) {
      if (typeof v !== 'boolean') continue
      if (offered.includes(k as LeverKey)) levers[k as LeverKey] = v ? 'on' : 'off'
      else unshown.push('a lever this site type does not offer')
    }
  }
  const optional = new Set<string>(OPTIONAL_STAGES)
  const pack: PackForm = {
    ...EMPTY_PACK_FORM,
    importMw: weak ? str(po.import_p_nom_mw) : '',
    importEnergy: weak ? str(po.import_energy_mwh_per_year) : '',
    budget: str(args.budget_solves),
    seed: str(mc.seed),
    dsrBuses: weak && Array.isArray(args.dsr_buses)
      ? args.dsr_buses.filter((b): b is string => typeof b === 'string').join(', ') : '',
    stages: Array.isArray(args.stages)
      ? args.stages.filter((s): s is string => typeof s === 'string' && optional.has(s)) : null,
    dtcAttribution: args.dtc_attribution === 'per_load' || args.dtc_attribution === 'bus_aggregate_not_per_load'
      ? args.dtc_attribution : '',
    levers,
  }
  const goal: GoalEdits = {}
  if (typeof po.ens_cap_permyriad === 'number') goal.ens_cap_permyriad = str(po.ens_cap_permyriad)
  if (typeof po.target_lole_h === 'number') goal.target_lole_h = str(po.target_lole_h)
  if (typeof mc.draws === 'number') goal.mc_draws = str(mc.draws)
  const changesGoal = Object.keys(goal).length > 0
  return { dest: changesGoal ? 'goal' : 'study', pack, ...(changesGoal ? { goal } : {}), unshown }
}

/** What "Open in Expert" does for one finding. A finding with no action
 *  opens the place its advice names (or the study, which shows its note). */
export function improvePlan(f: EhReviewFinding, archetype: EhArchetype): ImprovePlan {
  const a = f.actions?.[0]
  const later = (f.actions?.length ?? 0) > 1 ? ['the further steps (one at a time)'] : []
  if (a?.tool === 'run_eh_study' && isObj(a.args)) {
    const plan = planFromRerun(a.args, archetype)
    return { ...plan, unshown: [...plan.unshown, ...later] }
  }
  if (a?.tool === 'update_solver_config' && isObj(a.args) && isObj(a.args.partial)) {
    const { voll, ...rest } = a.args.partial
    const unshown = Object.keys(rest).length > 0 ? ['another solver setting', ...later] : later
    return typeof voll === 'number' && Number.isFinite(voll)
      ? { dest: 'voll', voll, unshown }
      : { dest: 'voll', unshown: ['the value to set', ...unshown] }
  }
  if (a) return { dest: 'study', unshown: ['the change itself', ...later] }
  // "Raise candidates' p_nom_max or add firm / storage candidates."
  if (f.id === 'ens_target_missed') return { dest: 'bounds', unshown: [] }
  return { dest: 'study', unshown: [] }
}

// ── The one-shot hand-over (GK B3 E3) ────────────────────────────────────

export interface ImprovePrefill {
  /** The project it was made for; another project never reads it. */
  project: string
  /** The finding's title, for "Filled in from the Improve card: …". */
  title: string
  pack?: PackForm
  goal?: GoalEdits
  voll?: number
}

interface PrefillState {
  prefill: ImprovePrefill | null
  set: (p: ImprovePrefill) => void
  clear: () => void
}

export const useImprovePrefillStore = create<PrefillState>(set => ({
  prefill: null,
  set: p => set({ prefill: p }),
  clear: () => set({ prefill: null }),
}))

/** Take one part of the prefill, once, for `project`. A prefill made for
 *  another project is dropped, never applied. */
export function takeImprovePrefill<K extends 'pack' | 'solver'>(project: string | null, part: K):
  (K extends 'pack' ? { title: string; pack: PackForm } : { title: string; goal?: GoalEdits; voll?: number }) | null {
  const s = useImprovePrefillStore.getState()
  const p = s.prefill
  if (!p) return null
  if (!project || p.project !== project) { s.clear(); return null }
  if (part === 'pack') {
    if (!p.pack) return null
    const { pack, ...rest } = p
    useImprovePrefillStore.setState({ prefill: rest.goal || rest.voll !== undefined ? rest : null })
    return { title: p.title, pack } as never
  }
  if (!p.goal && p.voll === undefined) return null
  const { goal, voll, ...rest } = p
  useImprovePrefillStore.setState({ prefill: rest.pack ? rest : null })
  return { title: p.title, ...(goal ? { goal } : {}), ...(voll !== undefined ? { voll } : {}) } as never
}

/** "Open in Expert": hand the plan over (nothing is written) and open its
 *  place in Expert, with a way back to the Improve card. */
export function openImproveInExpert(title: string, plan: ImprovePlan): void {
  const ui = useUIStore.getState()
  const project = ui.currentProject
  if (project && (plan.pack || plan.goal || plan.voll !== undefined)) {
    useImprovePrefillStore.getState().set({
      project, title,
      ...(plan.pack ? { pack: plan.pack } : {}),
      ...(plan.goal ? { goal: plan.goal } : {}),
      ...(plan.voll !== undefined ? { voll: plan.voll } : {}),
    })
  }
  withReturnFrom(() => {
    const s = useUIStore.getState()
    s.setUiMode('expert')
    if (plan.dest === 'study') {
      s.setSlidePanel('results')
      s.requestResultsTab('adequacy')
    } else if (plan.dest === 'bounds') {
      s.setSlidePanel('capacityBounds')
    } else {
      s.setSlidePanel('simparams')
      s.requestSolverBlock(plan.dest === 'goal' ? 'goal' : 'voll')
    }
  }, 'Improve')
}

/** "Open the stress scenarios": the Goal card's editor, "More settings"
 *  open, in Guided (where the Improve card is), with Back to Improve. */
export function openStressScenarios(): void {
  withReturnFrom(() => {
    useHubDesignStore.getState().setGoalAdvancedRequested(true)
    openSectionInGuided('scenarios')
  }, 'Improve')
}

/** Dropped when the user changes (AuthProvider's user-scoped reset). */
export function clearImprovePrefill(): void { useImprovePrefillStore.getState().clear() }

// A project switch drops a prefill made for the project left.
useUIStore.subscribe((s, prev) => {
  if (s.currentProject !== prev.currentProject) clearImprovePrefill()
})
