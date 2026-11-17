import { useEffect, useState } from 'react'
import { useRunEstimate } from '../../hooks/useRunEstimate'
import { etaText, clock, runStatusText, type RunView } from '../../utils/runStatus'

/**
 * One run's status line (run progress spec §1, slice 2): which run, its stage,
 * its steps, the elapsed time and the estimate.
 *
 * Narrow windows drop parts in the UX-agreed order: the ETA first (below
 * 768 px), then the step count (below 640 px). The name, stage and elapsed
 * time stay, and the full line is the tooltip. The elapsed clock is not
 * announced; a visually hidden `role="status"` region carries only the name
 * and stage, so a screen reader hears a stage change and nothing every second.
 */
export function RunStatusLine({ run }: { run: RunView }) {
  const now = useNow(run.startedAt != null)
  const estimate = useRunEstimate(run.queued ? null : run.jobId)
  const elapsed = run.startedAt != null ? Math.max(0, now - run.startedAt) : null
  const eta = run.jobId && !run.queued ? etaText(estimate.data, elapsed ?? 0) : null
  const full = runStatusText(run, elapsed, eta)
  return (
    <span data-testid="run-status" title={eta?.basis ? `${full}\n${eta.basis}` : full}
      className="flex min-w-0 items-center gap-1.5">
      <span className="w-1.5 h-1.5 rounded-full shrink-0 bg-warn motion-safe:animate-pulse" aria-hidden />
      <span className="sr-only" role="status">{`${run.name}: ${run.stage}`}</span>
      <span aria-hidden className="flex min-w-0 items-center gap-1 whitespace-nowrap">
        <span className="font-semibold">{run.name}</span>
        <span>· {run.stage}</span>
        {run.steps && <span data-testid="run-status-steps" className="hidden sm:inline">· {run.steps}</span>}
        {elapsed != null && <span data-testid="run-status-elapsed" className="font-mono">· {clock(elapsed)}</span>}
        {eta && (
          <span data-testid="run-status-eta" className={`hidden md:inline truncate ${eta.muted ? 'opacity-70' : ''}`}>
            · {eta.text}
          </span>
        )}
      </span>
    </span>
  )
}

/** Epoch seconds, ticking once a second while `active`. */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now() / 1000)
  useEffect(() => {
    if (!active) return
    setNow(Date.now() / 1000)
    const id = setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => clearInterval(id)
  }, [active])
  return now
}
