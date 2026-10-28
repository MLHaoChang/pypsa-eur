// A result the solve did not record (a missing or non-finite capture value)
// reads "—", never 0. The "—" alone is ambiguous, so every one carries the
// same hover text and accessible name, and each page that shows one explains
// it once in visible text under its KPI row or table (UX review, A1.6 / A1.7).

export const NOT_RECORDED = 'Not recorded by this solve'

/** KPI props for a figure this solve did not record: "—" with the label. */
export const NOT_RECORDED_KPI = { value: '—', valueLabel: NOT_RECORDED } as const

/** The "—" for one value this solve did not record (table cell, inline text). */
export function NotRecordedDash() {
  return <span role="img" title={NOT_RECORDED} aria-label={NOT_RECORDED}>—</span>
}

/** The page's one visible explanation of "—"; renders only when `show`. */
export function NotRecordedNote({ show }: { show: boolean }) {
  if (!show) return null
  return (
    <p className="text-[11px] text-muted" data-testid="not-recorded-note">
      —: not recorded by this solve
    </p>
  )
}
