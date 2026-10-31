import { EDITED_TEXT } from '../hubDesign/cards/ResultsCard'

/** The Energy Hub results card's amber "edited since this study" line, for the
 *  frontier, MC and margin-loop panels (LC-41). Shown only on `true`: `null`
 *  means running or no captured revision, which is not a stale claim. */
export function EditedSinceStudyBanner({ edited, testId }: {
  edited: boolean | null | undefined
  testId: string
}) {
  if (edited !== true) return null
  return (
    <div className="mx-3 my-1">
      <p data-testid={testId}
        className="rounded border border-warn/50 bg-warn/10 px-3 py-2 text-[12px] text-warn">
        {EDITED_TEXT}
      </p>
    </div>
  )
}
