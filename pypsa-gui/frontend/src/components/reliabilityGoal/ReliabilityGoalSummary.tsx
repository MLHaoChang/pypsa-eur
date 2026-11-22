// The reliability goal, read-only, where a Results study runs (W3b-R): the
// values the study will use, then a "›" link to the one editor (Guided's Goal
// card, Expert's Solver settings) and, where the study reads it, to the site
// type. The study sends none of these: the backend fills them from the goal.
import type { GoalValue } from '../../api/flow'
import { openReliabilityGoal, openSiteType } from '../../layout/flowNavigation'
import { ASSUMED_GOAL_SOURCE, assumedLole, goalWords, MAX_GOAL_DRAWS, useReliabilityGoal } from '../../hooks/useReliabilityGoal'
import { AssumedChip } from '../AssumedChip'
import { siteTypeLabel } from './SiteTypeSelect'

export const NO_GOAL = 'No reliability goal set.'
/** A stored 0 h/yr is a valid goal (the EH study certifies against it), but a
 *  loop searches for the margin that meets a shortfall above zero. */
export const ZERO_TARGET = 'The loop needs an allowed shortfall above 0 h/yr.'

export type GoalItem = 'target' | 'ens' | 'draws' | 'mc_budget' | 'site'

/** The sequential MC's stopping rule (`mc_adequacy`'s default `cov_target`). */
export const MC_COV_TARGET = 0.05

// 0.05 → "5", never "5.000000000000001".
const pct = (v: number): string => (v * 100).toLocaleString('en-GB', { maximumFractionDigits: 2 })

const sourceNote = (f: GoalValue<unknown>): string =>
  f.source === 'template' ? ' (template)' : f.source === 'default' ? ' (default)' : ''

/** True when the goal has an allowed shortfall a loop can search against. */
export function hasTarget(target: number | null): target is number {
  return target != null && Number.isFinite(target) && target > 0
}

export function ReliabilityGoalSummary({ items, testId, requireTarget = false, mcBudget = null }: {
  items: GoalItem[]
  testId: string
  /** `mc_budget` only: the last run's own batching (`result.draws`), read
   *  instead of the defaults when a run carries it. */
  mcBudget?: { first_batch: number; max: number; cov_target: number } | null
  /** A study that cannot run without a shortfall target (the loops): with
   *  none (or 0), say so and link to where it is set instead of the values.
   *  A goal that could not be read falls through to "could not be read". */
  requireTarget?: boolean
}) {
  const { goal, target, isPending, isError } = useReliabilityGoal()
  const link = 'text-link hover:underline'
  if (requireTarget && goal && !hasTarget(target)) {
    return (
      <div data-testid={testId} className="flex flex-wrap items-center gap-x-2 text-[10px]">
        <span data-testid={`${testId}-no-goal`} className="text-warn">
          {target === 0 ? ZERO_TARGET : NO_GOAL}</span>
        <button type="button" data-testid={`${testId}-set`} className={link}
          onClick={openReliabilityGoal}>Set it in Scenarios &amp; optimisation ›</button>
      </div>
    )
  }
  // The same words the editor shows in both modes (`goalWords`).
  const parts: string[] = []
  // A goal nobody set reads the site type's, marked with the shared chip.
  const assumed = items.includes('target') && goal?.target_lole_h.value == null ? assumedLole(goal) : null
  if (goal) {
    for (const it of items) {
      if (it === 'target') parts.push(`allowed shortfall ${assumed != null ? `${assumed} h/yr`
        : goalWords(goal, 'target_lole_h')}`)
      else if (it === 'ens') parts.push(`ENS target ${goalWords(goal, 'ens_cap_permyriad')}`)
      else if (it === 'draws') parts.push(`MC draws ${goalWords(goal, 'mc_draws')}`)
      // The Expert MC batches adaptively (walkthrough asset-investment 08):
      // the goal's draws are its first batch, not its whole run.
      else if (it === 'mc_budget') parts.push(mcBudget
        ? `MC draws ${mcBudget.first_batch.toLocaleString('en-GB')} first, then more until the estimate's `
          + `spread is under ${pct(mcBudget.cov_target)} %, up to ${mcBudget.max.toLocaleString('en-GB')}`
        : `MC draws ${goalWords(goal, 'mc_draws')} first, then more `
          + `until the estimate's spread is under ${pct(MC_COV_TARGET)} %, up to `
          + MAX_GOAL_DRAWS.toLocaleString('en-GB'))
      else {
        // The select's own label and wording ("Grid connection: None — …").
        const l = siteTypeLabel(goal.site_archetype.value)
        parts.push(`grid connection: ${l[0].toLowerCase()}${l.slice(1)}${sourceNote(goal.site_archetype)}`)
      }
    }
  }
  // Inline text, not a wrapping flex row: a long row wraps inside its words
  // and each link follows the values on the same line, as its own element.
  const inlineLink = `${link} ml-2 whitespace-nowrap`
  return (
    <p data-testid={testId} className="text-[10px] text-muted">
      <span className="font-semibold">Reliability goal:</span>{' '}
      <span data-testid={`${testId}-values`} className="font-mono">
        {goal ? parts.join(' · ') : isError ? 'could not be read' : '…'}
      </span>
      {assumed != null && (
        <>{' '}<AssumedChip source={ASSUMED_GOAL_SOURCE} testId={`${testId}-assumed`} /></>
      )}
      <button type="button" data-testid={`${testId}-change`} className={inlineLink}
        onClick={openReliabilityGoal}>Change in Scenarios &amp; optimisation ›</button>
      {items.includes('site') && (
        <button type="button" data-testid={`${testId}-site`} className={inlineLink}
          onClick={openSiteType}>Site type in Connection &amp; grid ›</button>
      )}
    </p>
  )
}
