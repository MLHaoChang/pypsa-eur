/** The units each credited grid service comes from (DC grid-readiness
 *  walkthrough 03), shared by the scorecard's frequency-support value and the
 *  architecture compare's cells. A service with more than five units folds
 *  after five, with "(N more) Show all" on click, as the unrecorded-series
 *  banner does. `rest` is the value's text after the named services (the
 *  architecture's assumed and conditional services). */
import { useState } from 'react'
import type { GridReadinessServiceSource } from '../../api/campusElectrical'

const SHOWN_AT_FIRST = 5

function Units({ units, testId }: { units: string[]; testId: string }) {
  const [open, setOpen] = useState(false)
  const folded = units.length > SHOWN_AT_FIRST && !open
  return (
    <>
      {(folded ? units.slice(0, SHOWN_AT_FIRST) : units).join(', ')}
      {units.length > SHOWN_AT_FIRST && (
        <button type="button" data-testid={testId} aria-expanded={open}
          className="ml-1 text-accent hover:underline" onClick={() => setOpen(o => !o)}>
          {open ? 'Show fewer' : `(${units.length - SHOWN_AT_FIRST} more) Show all`}
        </button>
      )}
    </>
  )
}

export function ServiceSources({ sources, rest, testId }: {
  sources: GridReadinessServiceSource[]; rest?: string | null; testId: string
}) {
  return (
    <span data-testid={testId}>
      {sources.map((s, i) => (
        <span key={s.service}>
          {i > 0 && ' · '}{s.label} (<Units units={s.units} testId={`${testId}-toggle-${s.service}`} />)
        </span>
      ))}
      {rest ? `${sources.length ? '; ' : ''}${rest}` : null}
    </span>
  )
}
