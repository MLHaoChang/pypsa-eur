// The one reliability goal editor (UX-flow audit row 1, W3b-R): allowed
// shortfall, energy strictness (the ENS target) and MC draws. Controlled: the
// parent holds what was typed (`edits`, only the edited fields) and decides
// how it is written — Guided's Goal card with its "Save goal", Expert's Solver
// settings through its draft and Save. An unedited field shows the stored or
// template value from the goal, so both modes show the same goal.
// The CO₂ limit (DC review 02) sits below the reliability fields: it is saved
// with them, but shows even where the reliability fields are out of scope.
import type { ReactNode } from 'react'
import type { ReliabilityGoal } from '../../api/flow'
import {
  assumedLole, CO2_LIMIT, CO2_LIMIT_UNIT, goalPatchFromEdits, goalText, goalWords, GOAL_UNSET, MAX_GOAL_DRAWS,
  type GoalEdits, type GoalNumberField,
} from '../../hooks/useReliabilityGoal'
import { useProjectFlow } from '../../hooks/useProjectFlow'
import { isAssumedItem } from '../../pages/hubDesign/AssumptionsCard'
import {
  GOAL_KEY_KEPT, GOAL_KEY_SITE_TYPE, GOAL_KEY_TEMPLATE, GOAL_KEY_TYPED,
} from '../../pages/hubDesign/cards/goalCardWords'
import { AssumedChip } from '../AssumedChip'
import { Term } from '../../pages/hubDesign/shared/Term'
import { useGuide } from '../GuidedTour'
import { InfoTip } from '../../layout/properties/cardKit'

/** The ENS target's hint, as Solver settings has always worded it. */
export const ENS_HINT = "Caps unserved ELECTRICAL energy per investment period at this share (parts per ten thousand) of the period's demand. 0 = off. Adequate systems run ~0.1–1‱; GB's standard is 3 loss-of-load hours/yr. Requires a VOLL > 0."
export const ENS_UNIT = '‱ of demand'

const ALL_FIELDS: GoalNumberField[] = ['target_lole_h', 'ens_cap_permyriad', 'mc_draws']

/** The CO₂ limit's label and its hint for an empty field (UX GO on DC 02). */
export const CO2_LIMIT_LABEL = 'CO₂ limit'
export const CO2_LIMIT_HINT = 'No limit. Type the most CO₂ the design may emit per year to make the optimiser meet it.'
export const CO2_LIMIT_ZERO = 'A limit of 0: the design may emit no CO₂ at all.'

export function ReliabilityGoalEditor({
  mode, goal, edits, onChange, fields = ALL_FIELDS, disabled = false, co2Limit,
}: {
  /** Its two homes: Guided's Goal card (`hub-goal-*` test ids) and Expert's
   *  Solver settings (`solver-goal-*`). */
  mode: 'guided' | 'expert'
  goal: ReliabilityGoal | null
  edits: GoalEdits
  onChange: (edits: GoalEdits) => void
  /** The fields shown (Guided keeps ENS and draws under "More settings"). */
  fields?: GoalNumberField[]
  disabled?: boolean
  /** The stored CO₂ limit (t CO₂/yr, null = none); the field shows only
   *  when this is given (undefined while the config loads). */
  co2Limit?: number | null
}) {
  const guided = mode === 'guided'
  // The CO₂ limit says its own error under its field.
  const { [CO2_LIMIT]: _co2, ...reliabilityEdits } = edits
  const error = goalPatchFromEdits(reliabilityEdits).error
  // The catalogue's hover help (P21), as the EH panel's pack fields had it.
  // Guided's labels are glossary terms with their own hover; draws has none.
  const { data: guide } = useGuide()
  const fieldTip = (key: string): string | undefined => guide?.fields?.[key]
  const lole = useLoleStanding(goal, edits)

  const label: Record<GoalNumberField, ReactNode> = guided ? {
    target_lole_h: <Term k="shortfall_hours">Allowed shortfall (hours per year)</Term>,
    ens_cap_permyriad: <><Term k="energy_strictness">Energy strictness</Term> ({ENS_UNIT})</>,
    mc_draws: <InfoTip text={fieldTip('mc_draws')}>Simulation runs per check</InfoTip>,
  } : {
    target_lole_h: <InfoTip text={fieldTip('target_lole_h')}>Allowed shortfall (h/yr)</InfoTip>,
    ens_cap_permyriad: <InfoTip text={fieldTip('ens_cap_permyriad')}>{`ENS target (${ENS_UNIT})`}</InfoTip>,
    mc_draws: <InfoTip text={fieldTip('mc_draws')}>MC draws</InfoTip>,
  }
  // An empty field reads what the goal says, the same in both modes and in
  // the Results summary: `not set`, `off`, or the default draws.
  // The site type's assumed goal fills an empty allowed shortfall: the value
  // the study certifies against until the user types one (template-e2e 06).
  const assumed = assumedLole(goal)
  const placeholder = (k: GoalNumberField): string =>
    (k === 'mc_draws' ? goalWords(goal, k) || 'default' : GOAL_UNSET[k])
  const shown = (k: GoalNumberField): string =>
    goalText(goal, k) || (k === 'target_lole_h' && assumed !== null ? String(assumed) : '')
  const common = (k: GoalNumberField) => ({
    k, mode, label: label[k], placeholder: placeholder(k), disabled,
    value: edits[k] ?? shown(k),
    // The allowed shortfall says where its value comes from with the chip.
    fromTemplate: k !== 'target_lole_h' && !(k in edits) && goal?.[k].source === 'template',
    standing: k === 'target_lole_h' ? lole : null,
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
      {co2Limit !== undefined && (
        <Co2LimitField mode={mode} co2Limit={co2Limit} edits={edits} onChange={onChange} disabled={disabled} />
      )}
    </div>
  )
}

const HINT: Partial<Record<GoalNumberField, string>> = {
  ens_cap_permyriad: ENS_HINT,
  mc_draws: `Samples of the sequential Monte Carlo every reliability study runs (1–${MAX_GOAL_DRAWS}).`,
}

/** Where the allowed shortfall's value comes from, for its chip: typed (or
 *  saved) by the user, the template's, or the site type's assumed goal; null
 *  with no value at all. Kept: the user confirmed the template's value with
 *  "Keep all defaults" (its `assumed_*` flow item is acknowledged). */
type LoleStanding = { typed: true } | { typed: false; from: 'template' | 'site_type'; value: number; kept: boolean }

function useLoleStanding(goal: ReliabilityGoal | null, edits: GoalEdits): LoleStanding | null {
  const { flow } = useProjectFlow()
  const t = goal?.target_lole_h
  if (!t) return null
  if ('target_lole_h' in edits) {
    if ((edits.target_lole_h ?? '').trim() !== '') return { typed: true }
  } else if (t.source === 'stored' && t.value != null) return { typed: true }
  const kept = (flow?.sections ?? []).some(s => s.items.some(it =>
    isAssumedItem(it) && it.target.field === 'target_lole_h' && it.acknowledged))
  if (t.source === 'template' && t.value != null) return { typed: false, from: 'template', value: t.value, kept }
  const assumed = assumedLole(goal)
  return assumed === null ? null : { typed: false, from: 'site_type', value: assumed, kept }
}

function LoleChip({ testId, standing }: { testId: string; standing: LoleStanding }) {
  const typed = standing.typed
  return (
    <span className="flex flex-col gap-0.5 text-[10px]">
      <AssumedChip testId={`${testId}-assumed`} typed={typed}
        confirmed={!standing.typed && standing.kept}
        source={standing.typed ? null : standing.from === 'template' ? 'Project template' : 'Site type'}
        note={standing.typed ? null : `${standing.value} h/yr`} />
      <span data-testid={`${testId}-key`} className="text-muted">
        {standing.typed ? GOAL_KEY_TYPED : standing.kept ? GOAL_KEY_KEPT
          : standing.from === 'template' ? GOAL_KEY_TEMPLATE : GOAL_KEY_SITE_TYPE}
      </span>
    </span>
  )
}

function GoalInput({ k, mode, testId, label, placeholder, value, disabled, fromTemplate, standing, onChange }: {
  k: GoalNumberField
  mode: 'guided' | 'expert'
  testId: string
  label: ReactNode
  placeholder: string
  value: string
  disabled: boolean
  fromTemplate: boolean
  standing: LoleStanding | null
  onChange: (v: string) => void
}) {
  const guided = mode === 'guided'
  const hint = HINT[k]
  return (
    <label className={guided ? 'flex flex-col gap-1 text-[12px]' : 'flex flex-col gap-1'}>
      <span className={guided ? 'font-semibold text-text' : 'text-[10px] text-muted'} title={hint}>
        {/* One ⓘ: the label's catalogue hover (its InfoTip); the hint is the title. */}
        {label}
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
      {standing && <LoleChip testId={testId} standing={standing} />}
    </label>
  )
}

/** The CO₂ limit (DC review 02), part of the goal and saved with it: empty =
 *  no limit, with a hint; 0 is a limit (no emissions at all), said on a muted
 *  line; an invalid value says so under the field. The editor mounts it below
 *  the reliability fields; Guided's Goal card places it itself, below "More
 *  settings", so it stays when the reliability goal is out of scope. */
export function Co2LimitField({ mode, co2Limit, edits, onChange, disabled = false }: {
  mode: 'guided' | 'expert'
  /** The stored limit, null = none. */
  co2Limit: number | null
  edits: GoalEdits
  onChange: (edits: GoalEdits) => void
  disabled?: boolean
}) {
  const guided = mode === 'guided'
  const testId = guided ? 'hub-goal-co2' : 'solver-goal-co2'
  const value = edits[CO2_LIMIT] ?? (co2Limit == null ? '' : String(co2Limit))
  const v = value.trim()
  const error = goalPatchFromEdits({ [CO2_LIMIT]: value }).error
  const note = error ? null : v === '' ? CO2_LIMIT_HINT : Number(v) === 0 ? CO2_LIMIT_ZERO : null
  return (
    <label className={guided ? 'flex flex-col gap-1 text-[12px]' : 'flex flex-col gap-1'}>
      <span className={guided ? 'font-semibold text-text' : 'text-[10px] text-muted'}>
        {CO2_LIMIT_LABEL} ({CO2_LIMIT_UNIT})
      </span>
      <input type="number" data-testid={testId} step="any" min={0}
        value={value} disabled={disabled} placeholder="no limit"
        onChange={e => onChange({ ...edits, [CO2_LIMIT]: e.target.value })}
        className={guided
          ? 'w-32 rounded border border-border bg-bg px-2 py-1 font-mono text-[12px] text-text'
          : 'px-2 py-1 border border-border rounded text-xs font-mono bg-bg focus:outline-none focus:border-accent'} />
      {error && (
        <span data-testid={`${testId}-error`} className={guided ? 'text-[12px] text-warn' : 'text-[10px] text-warn'}>
          {error}
        </span>
      )}
      {note && (
        <span data-testid={`${testId}-note`} className={guided ? 'text-[12px] text-muted' : 'text-[10px] text-muted'}>
          {note}
        </span>
      )}
    </label>
  )
}
