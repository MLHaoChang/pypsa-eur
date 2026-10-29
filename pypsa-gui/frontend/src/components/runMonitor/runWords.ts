// Study harness issue 08: a run's plan card in the user's words (design §10,
// UX conditions 2, 7 and 8). The backend's ids stay internal; nothing here
// shows a raw key. `backend/tests/test_harness_run_monitor.py` checks that
// every profile envelope's component, field and run tool has words here.
import type { EnvelopeEdit, EnvelopeRun } from '../../api/runMonitor'
import { COMPONENT_LABEL } from '../../utils/componentLabels'
import { FIELD_LABELS } from '../../utils/fieldLabels'
import { displayName } from '../../utils/displayName'

/** The stage names (design §10), in run order. */
export const STAGE_WORDS: Record<string, string> = {
  intake: 'Brief', setup: 'Set up', inputs: 'Inputs', input_gate: 'Check inputs', solve: 'Solve',
  result_gate: 'Check results', explore: 'Explore options', report: 'Report', handoff: 'Hand-off',
}

export const STAGE_STATUS_WORDS: Record<string, string> = {
  pending: 'to do', active: 'now', passed: 'done', failed: 'failed', skipped: 'skipped',
}

export const stageWords = (stage: string): string => STAGE_WORDS[stage] ?? 'A later step'

/** A gate rule by the fact it reads (`services/run_gates.FACTS`). */
export const FACT_WORDS: Record<string, string> = {
  'brief.valid': 'The brief is complete',
  'profile.resolved': 'The study type is known',
  'run.mode': 'The run mode is set',
  'project.exists': 'The project exists',
  'project.template': 'The project type fits the study',
  'validate.errors': 'The model has no errors',
  'readiness.ready': 'The model is ready to solve',
  'diagnose.unserved_islands': 'Every load is connected',
  'solve.state': 'The solve finished',
  'solve.status': 'The solve found a solution',
  fresh: 'The results match the inputs',
  'ic.counterfactual': 'The comparison case exists',
  'ic.baseline_keeps': 'The comparison keeps the baseline',
}

/** The unit a counted gate fact is read in, when the rule sends none,
 *  singular and plural: "Every load is connected · 1 unconnected load group". */
export const FACT_UNITS: Record<string, { one: string; other: string }> = {
  'validate.errors': { one: 'error', other: 'errors' },
  'diagnose.unserved_islands': { one: 'unconnected load group', other: 'unconnected load groups' },
}

/** A counted gate fact's unit for `n`: "1 error", "2 errors". */
export function factUnit(fact: string, n: number): string | undefined {
  const unit = FACT_UNITS[fact]
  return unit && (n === 1 ? unit.one : unit.other)
}

/** An envelope edit's component class: the panels' own word (`COMPONENT_LABEL`).
 *  Written as one entry per class so the backend's parity test
 *  (`test_harness_run_monitor.py`) can read the keys from this file. */
export const COMPONENT_WORDS: Record<string, { words: string }> = {
  Generator: { words: COMPONENT_LABEL.Generator },
  StorageUnit: { words: COMPONENT_LABEL.StorageUnit },
  Store: { words: COMPONENT_LABEL.Store },
  Link: { words: COMPONENT_LABEL.Link },
  Load: { words: COMPONENT_LABEL.Load },
}

/** The names a profile's envelope globs over, in words. */
export const NAME_WORDS: Record<string, string> = {
  'genset_new_*': 'New gensets', 'pv_new_*': 'New PV', 'bess_new_*': 'New batteries',
}

/** An envelope edit's field: the panels' own word (`FIELD_LABELS`) and its unit. */
export const FIELD_WORDS: Record<string, { words: string; unit: string }> = {
  p_nom: { words: FIELD_LABELS.p_nom, unit: 'MW' },
  p_nom_max: { words: FIELD_LABELS.p_nom_max, unit: 'MW' },
  p_nom_min: { words: FIELD_LABELS.p_nom_min, unit: 'MW' },
  max_hours: { words: FIELD_LABELS.max_hours, unit: 'h' },
  marginal_cost: { words: FIELD_LABELS.marginal_cost, unit: '€/MWh' },
}

/** The runs an envelope can cover. */
export const RUN_WORDS: Record<string, string> = {
  solve_queue_enqueue: 'Queued solves', run_simulation: 'Solves', run_ac_pf_stage: 'Load-flow checks',
}

const number = (x: number) => (Number.isInteger(x) ? String(x) : x.toFixed(2).replace(/\.?0+$/, ''))

/** "New gensets · Maximum capacity" */
export function editSubject(edit: EnvelopeEdit): string {
  const { component, name, attr } = edit.target
  const who = NAME_WORDS[name] ?? `${COMPONENT_WORDS[component]?.words ?? 'Asset'} “${displayName(name)}”`
  return `${who} · ${FIELD_WORDS[attr]?.words ?? 'A design value'}`
}

/** "0 MW or more, within ±50 % of today's value" · "1 h to 8 h" · "one of: a, b" */
export function editLimits(edit: EnvelopeEdit): string {
  if (edit.enum) return `one of: ${edit.enum.join(', ')}`
  const unit = FIELD_WORDS[edit.target.attr]?.unit
  const u = (x: number) => (unit ? `${number(x)} ${unit}` : number(x))
  const { min, max, rel } = edit.bounds ?? {}
  const parts: string[] = []
  if (min !== undefined && max !== undefined) parts.push(`${u(min)} to ${u(max)}`)
  else if (min !== undefined) parts.push(`${u(min)} or more`)
  else if (max !== undefined) parts.push(`up to ${u(max)}`)
  if (rel !== undefined) parts.push(`within ±${Math.round(rel * 100)} % of today's value`)
  return parts.join(', ') || 'no limit'
}

export const runWords = (run: EnvelopeRun): string => RUN_WORDS[run.tool] ?? 'Other runs'

/** "28 Oct 18:00", in the viewer's time zone. */
export function when(iso: string | null | undefined): string {
  if (!iso) return 'no expiry'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return 'no expiry'
  const day = d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
  const time = d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
  return `${day} ${time}`
}

/** Why "Approve and run unattended" cannot work here (UX condition 3). */
export const UNATTENDED_BLOCK_WORDS: Record<string, string> = {
  runner_off: 'Unattended runs are off on this server',
  no_provider: 'Needs a model key: chat is off',
  locked: 'Another user is editing this project',
  no_notice: 'Unattended needs a way to tell you when the run stops: email is not set up on this server, and in-app notices cannot be saved right now.',
}

/** The fix a stopped run offers: a link in words (UX condition 8).
 *  `issues`, `results` and `solve_log` open a panel; `budget`, `new_brief`
 *  and `review` ask the assistant on the user's click; `resume` resumes. */
export type RunFix = 'resume' | 'issues' | 'results' | 'solve_log' | 'budget' | 'new_brief' | 'review'

/** A run the runner stopped, in words, with its fix. `reason` is the
 *  backend's `run.reason`; an unknown one still reads in words. */
export function stopWords(reason: string | undefined, stage: string): { words: string; fix: RunFix | null; fixWords: string } {
  const at = stageWords(stage)
  switch (reason) {
    case 'user': return { words: `Paused at ${at}`, fix: 'resume', fixWords: 'Resume' }
    case 'budget': return { words: 'Cost budget used up: the run waits for you', fix: 'budget', fixWords: 'Change the budget' }
    case 'envelope_exhausted': return { words: 'Solve budget used up: the run waits for you', fix: 'budget', fixWords: 'Change the budget' }
    case 'envelope_expired': return { words: 'Unattended approval expired', fix: 'resume', fixWords: 'Continue step by step' }
    case 'envelope_revoked': return { words: 'Unattended approval withdrawn', fix: 'resume', fixWords: 'Continue step by step' }
    case 'envelope_drift':
    case 'envelope_fingerprint_unreadable':
      return { words: 'The project changed outside the run', fix: 'resume', fixWords: 'Continue step by step' }
    case 'brief_stale':
    case 'envelope_brief_stale':
      return { words: 'Inputs out of date', fix: 'new_brief', fixWords: 'Review a new brief' }
    case 'step_failed':
      return stage === 'solve'
        ? { words: 'Solve did not finish', fix: 'solve_log', fixWords: 'Open the solve log' }
        : { words: `A step in ${at} did not finish`, fix: 'review', fixWords: 'Review the step' }
    case 'uncertain':
    case 'step_uncertain':
      return { words: 'A step may or may not have run', fix: 'review', fixWords: 'Review the step' }
    case 'locked': return { words: 'Another user is editing this project', fix: 'resume', fixWords: 'Try again' }
    case 'access': return { words: 'You can no longer edit this project', fix: null, fixWords: '' }
    case 'restarts':
    case 'runner_errors':
    case 'recheck':
      return { words: 'The run stopped after repeated errors', fix: 'resume', fixWords: 'Try again' }
    default: return { words: `Stopped at ${at}`, fix: 'resume', fixWords: 'Resume' }
  }
}
