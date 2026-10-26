// The reliability goal, read-only, where a Results study runs (W3b-R): the
// values the study will use, then a "›" link to the one editor (Guided's Goal
// card, Expert's Solver settings) and, where the study reads it, to the site
// type. The study sends none of these: the backend fills them from the goal.
import type { GoalValue } from '../../api/flow'
import { openReliabilityGoal, openSiteType } from '../../layout/flowNavigation'
import { useReliabilityGoal } from '../../hooks/useReliabilityGoal'
import { siteTypeLabel } from './SiteTypeSelect'

export const NO_GOAL = 'No reliability goal set.'

export type GoalItem = 'target' | 'ens' | 'draws' | 'site'

const sourceNote = (f: GoalValue<unknown>): string =>
  f.source === 'template' ? ' (template)' : f.source === 'default' ? ' (default)' : ''

/** True when the goal has an allowed shortfall a loop can search against. */
export function hasTarget(target: number | null): target is number {
  return target != null && Number.isFinite(target) && target > 0
}

export function ReliabilityGoalSummary({ items, testId, requireTarget = false }: {
  items: GoalItem[]
  testId: string
  /** A study that cannot run without a shortfall target (the loops): with
   *  none, say so and link to where it is set instead of the values. */
  requireTarget?: boolean
}) {
  const { goal, target, isPending, isError } = useReliabilityGoal()
  const link = 'text-accent hover:underline'
  if (requireTarget && !isPending && !hasTarget(target)) {
    return (
      <div data-testid={testId} className="flex flex-wrap items-center gap-x-2 text-[10px]">
        <span data-testid={`${testId}-no-goal`} className="text-warn">{NO_GOAL}</span>
        <button type="button" data-testid={`${testId}-set`} className={link}
          onClick={openReliabilityGoal}>Set it in Scenarios &amp; optimisation ›</button>
      </div>
    )
  }
  const parts: string[] = []
  if (goal) {
    for (const it of items) {
      if (it === 'target') {
        const f = goal.target_lole_h
        parts.push(f.value == null ? 'allowed shortfall: the site type\'s own'
          : `allowed shortfall ${f.value} h/yr${sourceNote(f)}`)
      } else if (it === 'ens') {
        const f = goal.ens_cap_permyriad
        parts.push(f.value == null ? 'ENS target: the site type\'s own'
          : `ENS target ${f.value}‱${sourceNote(f)}`)
      } else if (it === 'draws') {
        parts.push(`${goal.mc_draws.value} MC draws${sourceNote(goal.mc_draws)}`)
      } else {
        parts.push(`site type: ${siteTypeLabel(goal.site_archetype.value)}${sourceNote(goal.site_archetype)}`)
      }
    }
  }
  return (
    <div data-testid={testId} className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-muted">
      <span className="font-semibold">Reliability goal:</span>
      <span data-testid={`${testId}-values`} className="font-mono">
        {goal ? parts.join(' · ') : isError ? 'could not be read' : '…'}
      </span>
      <button type="button" data-testid={`${testId}-change`} className={link}
        onClick={openReliabilityGoal}>Change in Scenarios &amp; optimisation ›</button>
      {items.includes('site') && (
        <button type="button" data-testid={`${testId}-site`} className={link}
          onClick={openSiteType}>Site type in Connection &amp; grid ›</button>
      )}
    </div>
  )
}
