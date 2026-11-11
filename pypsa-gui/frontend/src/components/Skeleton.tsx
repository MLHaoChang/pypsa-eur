// A loading placeholder that keeps the layout of what will replace it, so a
// panel does not jump and no value is shown before the real one arrives. It
// stays invisible for the first 200 ms so a fast load never flashes it, and
// the pulse is switched off under prefers-reduced-motion (`motion-safe`).
import { useEffect, useState } from 'react'

export const SKELETON_DELAY_MS = 200

function useDelayedShow(): boolean {
  const [shown, setShown] = useState(false)
  useEffect(() => {
    const t = setTimeout(() => setShown(true), SKELETON_DELAY_MS)
    return () => clearTimeout(t)
  }, [])
  return shown
}

export function Skeleton({ className = '', testId }: { className?: string; testId?: string }) {
  const shown = useDelayedShow()
  return (
    <span aria-hidden="true" data-testid={testId}
      className={`block rounded bg-panel-2 motion-safe:animate-pulse ${shown ? '' : 'invisible'} ${className}`} />
  )
}

/** A few skeleton lines (a busy `role="status"` region) with a screen-reader
 *  label that is not drawn. */
export function SkeletonLines({ what, rows = 3, testId }: { what: string; rows?: number; testId?: string }) {
  return (
    <div role="status" aria-busy="true" data-testid={testId} className="flex flex-col gap-2">
      <span className="sr-only">Loading {what}…</span>
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className={i === rows - 1 ? 'h-3 w-2/3' : 'h-3 w-full'} />
      ))}
    </div>
  )
}
