import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { retryOpen } from '../hooks/useProjectDeepLink'

// Quick opens finish before this and never show the strip (no flicker).
export const OPENING_STRIP_DELAY_MS = 2000
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/**
 * Progress strip shown while a project switch is in flight. Opening a cold
 * project can take a minute (large template, solve running); without this the
 * workbench sits on "Unnamed Network / No project open" with no sign of work.
 * The backend reports no percentage, so the bar is indeterminate. It appears
 * only after a short delay and overlays the top edge, so the header never moves.
 */
export default function ProjectOpeningBanner() {
  const opening = useUIStore(s => s.openingProject)
  const failure = useUIStore(s => s.openFailure)
  const qc = useQueryClient()
  const [late, setLate] = useState(false)
  useEffect(() => {
    setLate(false)
    if (!opening) return
    const t = window.setTimeout(() => setLate(true), OPENING_STRIP_DELAY_MS)
    return () => window.clearTimeout(t)
  }, [opening])
  if (failure && !opening) {
    const name = UUID_RE.test(failure.target) ? 'the project' : failure.target
    return (
      <div role="alert" data-testid="project-open-failed"
           className="fixed inset-x-0 top-0 z-50 flex items-center gap-2 border-b border-border bg-panel px-3 py-1.5 shadow">
        <div className="flex-1 text-[11px] text-red-500">Could not open {name}. {failure.message}</div>
        <button type="button" onClick={() => void retryOpen(qc)}
                className="rounded border border-border px-2 py-0.5 text-[11px] text-fg hover:bg-border/40">Retry</button>
        <button type="button" aria-label="Dismiss" onClick={() => useUIStore.getState().setOpenFailure(null)}
                className="px-1 text-[11px] text-muted">×</button>
      </div>
    )
  }
  if (!opening || !late) return null
  // A deep link by id carries a UUID, which means nothing to a reader.
  const label = UUID_RE.test(opening) ? 'project' : opening
  return (
    <div role="status" aria-live="polite" data-testid="project-opening"
         className="pointer-events-none fixed inset-x-0 top-0 z-50 border-b border-border bg-panel px-3 py-1.5 shadow">
      <div className="text-[11px] text-muted">Opening {label}… large projects can take a minute.</div>
      <div role="progressbar" aria-label={`Opening ${label}`}
           className="mt-1 h-0.5 w-full overflow-hidden rounded bg-border">
        <div className="h-full w-1/3 animate-pulse motion-reduce:animate-none rounded bg-accent" />
      </div>
    </div>
  )
}
