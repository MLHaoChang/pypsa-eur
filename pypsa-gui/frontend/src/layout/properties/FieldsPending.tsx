import { SkeletonLines, useDelayedShow } from '../../components/Skeleton'

/**
 * What a card or form shows while the bus units of its asset are read (05d): nothing for the first 200 ms, then
 * an `aria-busy` status line (#1335). A failed read says so and offers a retry: no cost can be typed or saved
 * under the class's per-kW scale on what may be a tonne bus, so the fields stay closed until the units arrive.
 */
export default function FieldsPending({ failed, onRetry, variant = 'card' }: {
  failed?: boolean; onRetry?: () => void; variant?: 'card' | 'line'
}) {
  const shown = useDelayedShow()
  if (failed) {
    return (
      <div role="alert" data-testid="units-failed" className="col-span-2 flex items-center gap-2 text-[10px] text-danger">
        <span>Could not read the units of this bus, so the cost fields stay closed.</span>
        {onRetry && (
          <button type="button" onClick={onRetry} className="underline text-accent">Retry</button>
        )}
      </div>
    )
  }
  if (variant === 'line') {
    return (
      <div role="status" aria-busy="true" data-testid="units-pending"
        className={`text-[10px] text-muted ${shown ? '' : 'invisible'}`}>
        Reading the units of the chosen bus…
      </div>
    )
  }
  return <div className="col-span-2"><SkeletonLines what="the bus units" rows={4} testId="units-pending" /></div>
}
