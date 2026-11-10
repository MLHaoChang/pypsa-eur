// A loading placeholder that keeps the layout of what will replace it, so a
// panel does not jump and no value is shown before the real one arrives. The
// pulse is switched off under prefers-reduced-motion (`motion-safe`).
export function Skeleton({ className = '', testId }: { className?: string; testId?: string }) {
  return (
    <span aria-hidden="true" data-testid={testId}
      className={`block rounded bg-panel-2 motion-safe:animate-pulse ${className}`} />
  )
}

/** A few skeleton lines under a visible "Loading <what>" label for a screen
 *  reader (`role="status"`); the label is not drawn. */
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
