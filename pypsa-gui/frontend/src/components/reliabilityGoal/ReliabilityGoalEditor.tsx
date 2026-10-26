// The one reliability goal editor (UX-flow audit row 1, W3b-R): allowed
// shortfall, energy strictness (the ENS target) and MC draws. Controlled: the
// parent holds what was typed (`edits`, only the edited fields) and decides
// how it is written — Guided's Goal card with its "Save goal", Expert's Solver
// settings through its draft and Save. An unedited field shows the stored or
// template value from the goal, so both modes show the same goal.
import type { ReactNode } from 'react'
import type { ReliabilityGoal } from '../../api/flow'
import {
  goalPatchFromEdits, goalText, MAX_GOAL_DRAWS, type GoalEdits, type GoalNumberField,
} from '../../hooks/useReliabilityGoal'
import { Term } from '../../pages/hubDesign/shared/Term'
import { useGuide } from '../GuidedTour'
import { InfoTip } from '../../layout/properties/cardKit'

/** The ENS target's hint, as Solver settings has always worded it. */
export const ENS_HINT = "Caps unserved ELECTRICAL energy per investment period at this share (parts per ten thousand) of the period's demand. 0 = off. Adequate systems run ~0.1–1‱; GB's standard is 3 loss-of-load hours/yr. Requires a VOLL > 0."
export const ENS_UNIT = '‱ of demand'

const ALL_FIELDS: GoalNumberField[] = ['target_lole_h', 'ens_cap_permyriad', 'mc_draws']

export function ReliabilityGoalEditor({
  mode, goal, edits, onChange, fields = ALL_FIELDS, defaults, disabled = false,
}: {
  /** Its two homes: Guided's Goal card (`hub-goal-*` test ids) and Expert's
   *  Solver settings (`solver-goal-*`). */
  mode: 'guided' | 'expert'
  goal: ReliabilityGoal | null
  edits: GoalEdits
  onChange: (edits: GoalEdits) => void
  /** The fields shown (Guided keeps ENS and draws under "More settings"). */
  fields?: GoalNumberField[]
  /** The site type's own values, named in the placeholder of a default field. */
  defaults?: { target_lole_h?: number | null; ens_cap_permyriad?: number | null }
  disabled?: boolean
}) {
  const guided = mode === 'guided'
  const error = goalPatchFromEdits(edits).error
  // The catalogue's hover help (P21), as the EH panel's pack fields had it.
  // Guided's labels are glossary terms with their own hover; draws has none.
  const { data: guide } = useGuide()
  const fieldTip = (key: string): string | undefined => guide?.fields?.[key]

  const label: Record<GoalNumberField, ReactNode> = guided ? {
    target_lole_h: <Term k="shortfall_hours">Allowed shortfall (hours per year)</Term>,
    ens_cap_permyriad: <><Term k="energy_strictness">Energy strictness</Term> ({ENS_UNIT})</>,
    mc_draws: <InfoTip text={fieldTip('mc_draws')}>Simulation runs per check</InfoTip>,
  } : {
    target_lole_h: <InfoTip text={fieldTip('target_lole_h')}>Allowed shortfall (h/yr)</InfoTip>,
    ens_cap_permyriad: <InfoTip text={fieldTip('ens_cap_permyriad')}>{`ENS target (${ENS_UNIT})`}</InfoTip>,
    mc_draws: <InfoTip text={fieldTip('mc_draws')}>MC draws</InfoTip>,
  }
  const placeholder = (k: GoalNumberField): string => {
    if (k === 'mc_draws') return `${goal?.mc_draws.value ?? 500} (default)`
    const d = defaults?.[k]
    if (d != null) return `${d} (default)`
    if (k === 'target_lole_h') return guided ? 'e.g. 3' : 'not set'
    return guided ? 'default' : 'off'
  }
  const common = (k: GoalNumberField) => ({
    k, mode, label: label[k], placeholder: placeholder(k), disabled,
    value: edits[k] ?? goalText(goal, k),
    fromTemplate: !(k in edits) && goal?.[k].source === 'template',
    onChange: (v: string) => onChange({ ...edits, [k]: v }),
  })
  const show = (k: GoalNumberField) => fields.includes(k)

  return (
    <div className="flex flex-col gap-2">
      {show('target_lole_h') && (guided
        ? <GoalInput testId="hub-goal-lole" {...common('target_lole_h')} />
        : <GoalInput testId="solver-goal-lole" {...common('target_lole_h')} />)}
      {show('ens_cap_permyriad') && (guided
        ? <GoalInput testId="hub-goal-ens" {...common('ens_cap_permyriad')} />
        : <GoalInput testId="solver-goal-ens" {...common('ens_cap_permyriad')} />)}
      {show('mc_draws') && (guided
        ? <GoalInput testId="hub-goal-draws" {...common('mc_draws')} />
        : <GoalInput testId="solver-goal-draws" {...common('mc_draws')} />)}
      {error && (guided
        ? <p data-testid="hub-goal-error" className="text-[12px] text-warn">{error}</p>
        : <p data-testid="solver-goal-error" className="text-[10px] text-warn">{error}</p>)}
    </div>
  )
}

const HINT: Partial<Record<GoalNumberField, string>> = {
  ens_cap_permyriad: ENS_HINT,
  mc_draws: `Samples of the sequential Monte Carlo every reliability study runs (1–${MAX_GOAL_DRAWS}).`,
}

function GoalInput({ k, mode, testId, label, placeholder, value, disabled, fromTemplate, onChange }: {
  k: GoalNumberField
  mode: 'guided' | 'expert'
  testId: string
  label: ReactNode
  placeholder: string
  value: string
  disabled: boolean
  fromTemplate: boolean
  onChange: (v: string) => void
}) {
  const guided = mode === 'guided'
  const hint = HINT[k]
  return (
    <label className={guided ? 'flex flex-col gap-1 text-[12px]' : 'flex flex-col gap-1'}>
      <span className={guided ? 'font-semibold text-text' : 'text-[10px] text-muted'} title={hint}>
        {label}
        {hint && !guided && <span className="ml-1 text-[9px] text-muted/60 cursor-help">ⓘ</span>}
      </span>
      <input type="number" data-testid={testId}
        step={k === 'mc_draws' ? 1 : 'any'} min={k === 'mc_draws' ? 1 : 0}
        max={k === 'mc_draws' ? MAX_GOAL_DRAWS : undefined}
        value={value} disabled={disabled} placeholder={placeholder}
        onChange={e => onChange(e.target.value)}
        className={guided
          ? 'w-32 rounded border border-border bg-bg px-2 py-1 font-mono text-[12px] text-text'
          : 'px-2 py-1 border border-border rounded text-xs font-mono bg-bg focus:outline-none focus:border-accent'} />
      {fromTemplate && (
        <span data-testid={`${testId}-template`} className="text-[10px] text-muted">
          From the project template
        </span>
      )}
    </label>
  )
}
