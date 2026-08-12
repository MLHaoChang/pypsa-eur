// Summary-first landing view for Model Horizon: one clickable line per
// visible step, each showing `stepSummary`'s sentence for the step's CURRENT
// configuration. A returning user reads this instead of re-opening every
// step. Real <button> rows (same interaction model as ProjectTabs' tab strip
// and PageKit's Seg) so the whole thing is keyboard-reachable.
import { stepSummary, type HorizonStepId, type HorizonSummaryContext } from '../modelHorizonModel'
import { STEP_LABELS } from './StepShell'

export interface HorizonSummaryProps {
  /** Steps visible in order — already filtered by visibleSteps(isMultiPeriod). */
  steps: HorizonStepId[]
  ctx: HorizonSummaryContext
  onOpen: (step: HorizonStepId) => void
}

export function HorizonSummary({ steps, ctx, onOpen }: HorizonSummaryProps) {
  return (
    <section
      aria-label="Model horizon summary"
      className="rounded-[10px] border border-border bg-bg overflow-hidden shadow-[0_1px_0_rgba(10,14,20,0.04)] divide-y divide-border"
    >
      {steps.map((step, i) => (
        <button
          key={step}
          type="button"
          onClick={() => onOpen(step)}
          className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-bg-2 transition-colors"
        >
          <span className="font-mono text-[10px] text-muted shrink-0">{i + 1}</span>
          <span className="text-[12px] font-medium text-text w-[168px] shrink-0">{STEP_LABELS[step]}</span>
          <span className="text-[11.5px] text-muted truncate">{stepSummary(step, ctx)}</span>
        </button>
      ))}
    </section>
  )
}
