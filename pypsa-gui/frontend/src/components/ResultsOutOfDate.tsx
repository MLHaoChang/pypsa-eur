// The results empty state after an input edit that follows a run: the backend withholds the figures
// (204 with `X-Results-State: out_of_date`). One sentence, one next step: the Run control, offered the
// way "Not run yet" offers it. Shared by every results surface (Expert tables, Summary, Guided Results).
import { StateAction } from './ResultState'
import { useUIStore } from '../store/uiStore'

/** Why and the next step, for a surface whose pill already says "Out of date". */
export const OUT_OF_DATE_REASON = 'The inputs changed after the last run. Run again to see the figures.'
export const OUT_OF_DATE_TEXT = `Out of date: ${OUT_OF_DATE_REASON.charAt(0).toLowerCase()}${OUT_OF_DATE_REASON.slice(1)}`

export default function ResultsOutOfDate() {
  const requestRunSolve = useUIStore(s => s.requestRunSolve)
  return (
    <div data-state="stale" data-testid="results-out-of-date" role="status"
      className="flex flex-col gap-1.5 rounded-[8px] border border-border bg-bg-2 px-3 py-2.5">
      <p className="text-[12px] leading-snug text-muted">{OUT_OF_DATE_TEXT}</p>
      <StateAction action={{ label: 'Run the solve', onClick: requestRunSolve }} />
    </div>
  )
}
