// A result the solve did not record (a missing or non-finite capture value)
// reads "—", never 0. The "—" alone is ambiguous, so every one carries the
// same hover text and accessible name, and each page that shows one explains
// it once in visible text under its KPI row or table (UX review, A1.6 / A1.7).
// A page whose sections each may show a "—" (Dispatch) opens a scope: the
// sections report to it, and the page renders the one note.

import { createContext, useCallback, useContext, useEffect, useId, useState } from 'react'

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

const NotRecordedReport = createContext<((id: string, shown: boolean) => void) | null>(null)

/** A page-level scope: `show` is true while any section in it shows a "—". */
export function useNotRecordedScope() {
  const [ids, setIds] = useState<ReadonlySet<string>>(() => new Set())
  const report = useCallback((id: string, shown: boolean) => {
    setIds(prev => {
      if (prev.has(id) === shown) return prev
      const next = new Set(prev)
      if (shown) next.add(id)
      else next.delete(id)
      return next
    })
  }, [])
  return { report, show: ids.size > 0, Provider: NotRecordedReport.Provider }
}

/** A section's note: inside a page scope it reports to the page's one note;
 *  on its own it renders the note itself. */
export function NotRecordedSectionNote({ show }: { show: boolean }) {
  const report = useContext(NotRecordedReport)
  const id = useId()
  useEffect(() => {
    if (!report) return
    report(id, show)
    return () => report(id, false)
  }, [report, id, show])
  return report ? null : <NotRecordedNote show={show} />
}
