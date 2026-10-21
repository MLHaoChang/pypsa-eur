// The period window control the energy-flow and load-shape tabs share: two
// range inputs over a period's snapshots, a label with the timestamps they
// bound, and a reset. `useWindowRange` holds the dragged value and hands the
// request the settled one after a pause, so a drag asks the server once.
import { useEffect, useState } from 'react'
import { shortStamp } from './shared'

export type WindowBounds = [number, number]

export function useWindowRange(delayMs = 250) {
  const [drag, setDrag] = useState<WindowBounds | null>(null)
  const [range, setRange] = useState<WindowBounds | null>(null)
  useEffect(() => {
    if (drag === null) return
    const id = setTimeout(() => setRange(drag), delayMs)
    return () => clearTimeout(id)
  }, [drag, delayMs])
  return {
    drag, range, setDrag,
    /** Back to the whole period (also what a change of period does). */
    clear: () => { setDrag(null); setRange(null) },
    whole: (snapshots: number) => { setDrag([0, snapshots - 1]); setRange(null) },
  }
}

export default function WindowSlider({ testId, snapshots, from, to, start, end, drag, setDrag, whole }: {
  testId: string
  snapshots: number
  /** The bounds the server last answered for. */
  from: number
  to: number
  start?: string | null
  end?: string | null
  drag: WindowBounds | null
  setDrag: (b: WindowBounds) => void
  whole: (snapshots: number) => void
}) {
  const lo = drag?.[0] ?? from
  const hi = drag?.[1] ?? to
  return (
    <div className="flex flex-wrap items-center gap-2 text-[11px]" data-testid={testId}>
      <span className="text-muted">Window</span>
      <input aria-label="Window start" type="range" min={0} max={snapshots - 1} value={lo}
             onChange={e => setDrag([Math.min(Number(e.target.value), hi), hi])} />
      <input aria-label="Window end" type="range" min={0} max={snapshots - 1} value={hi}
             onChange={e => setDrag([lo, Math.max(Number(e.target.value), lo)])} />
      <span data-testid={`${testId}-label`}>
        {start && end ? `${shortStamp(start)} → ${shortStamp(end)}` : `${lo} → ${hi}`}
        {' '}({hi - lo + 1} of {snapshots})
      </span>
      {(lo !== 0 || hi !== snapshots - 1) && (
        <button type="button" className="underline" onClick={() => whole(snapshots)}>
          Whole period
        </button>
      )}
    </div>
  )
}
