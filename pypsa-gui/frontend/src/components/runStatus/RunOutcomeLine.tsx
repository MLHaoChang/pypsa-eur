import { useState } from 'react'
import { CheckCircle2, AlertTriangle, X } from 'lucide-react'
import { useUIStore } from '../../store/uiStore'
import { outcomeReason, outcomeText, type RunOutcome } from '../../utils/runStatus'
import { openResults } from './openResults'

/**
 * The strip's line after a run ends (run progress slice 2b; audit #25). It
 * stays until dismissed or the next run starts, so "Open results" is never
 * only in a 6 s toast.
 *
 * - finished: "Open results" (section 10, in place in Guided; `openResults`);
 * - failed or interrupted: "See why". Expert opens the Solve queue, where the
 *   run's row holds its error and log; Guided shows the reason here, in plain
 *   words, without switching mode (UX plan GO 1);
 * - stopped by the user: the line alone.
 *
 * Text links are always underlined and keep the line's colour
 * (UI-COHESION-1). The live region is the strip's, so this line is not one.
 */
export function RunOutcomeLine({ outcome, onDismiss }: { outcome: RunOutcome; onDismiss: () => void }) {
  const expert = useUIStore(s => s.uiMode === 'expert')
  const setSlidePanel = useUIStore(s => s.setSlidePanel)
  const [why, setWhy] = useState(false)
  const done = outcome.end === 'completed'
  const failed = outcome.end === 'failed' || outcome.end === 'interrupted'
  const tone = done
    ? 'border-success/30 bg-success/10 text-success'
    : failed ? 'border-danger/30 bg-danger/10 text-danger' : 'border-border bg-bg-2 text-muted'
  const Icon = done ? CheckCircle2 : AlertTriangle
  const link = 'shrink-0 underline underline-offset-2 font-semibold'
  return (
    <div data-testid="run-outcome" data-end={outcome.end}
      className={`flex flex-wrap items-center gap-x-3 gap-y-0.5 px-3 py-1.5 min-h-8 shrink-0 border-b text-[11.5px] ${tone}`}>
      <span className="flex min-w-0 items-center gap-1.5">
        {outcome.end !== 'aborted' && <Icon size={13} className="shrink-0" aria-hidden />}
        <span data-testid="run-outcome-text" className="truncate">{outcomeText(outcome)}</span>
      </span>
      {done && (
        <button type="button" data-testid="run-outcome-open" className={link} onClick={openResults}>
          Open results
        </button>
      )}
      {failed && (
        <button type="button" data-testid="run-outcome-why" className={link}
          aria-expanded={expert ? undefined : why}
          onClick={() => (expert ? setSlidePanel('solveQueue') : setWhy(w => !w))}>
          See why
        </button>
      )}
      <button type="button" data-testid="run-outcome-dismiss" onClick={onDismiss}
        aria-label="Dismiss" title="Dismiss"
        className="ml-auto shrink-0 rounded p-0.5 opacity-70 hover:opacity-100">
        <X size={13} aria-hidden />
      </button>
      {failed && why && !expert && (
        <p data-testid="run-outcome-reason" className="basis-full text-text">{outcomeReason(outcome)}</p>
      )}
    </div>
  )
}
