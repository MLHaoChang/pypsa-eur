// The 3D view's results readout (Phase 2 spec §6.4, plan Task 6.3): while
// results show, each site object with its value at the snapshot, under the
// timestamp — so no result is colour-only or hover-only. DOM, no three; it
// subscribes to the results store itself, so SiteCanvas never re-renders
// for a snapshot step.
import { useCallback, useLayoutEffect, useRef } from 'react'
import { siteVisuals } from '../site3d/resultStyle'
import { useResultsSelector, type ResultsStore } from '../site3d/resultsStore'
import type { SiteResults } from '../site3d/useSiteResults'

/** Below this pane width the readout starts collapsed to its summary line (it would cover the scene). */
export const READOUT_OPEN_MIN_PX = 800

interface Props {
  store: ResultsStore
  objects: readonly { type: string; name: string; kind: string; bus: string }[]
}

export default function SiteResultsReadout({ store, objects }: Props) {
  const r = useResultsSelector(store, useCallback((x: SiteResults) => x, []))
  const visuals = siteVisuals(r.states, objects)
  const showing = visuals.size > 0
  // Open by default only in a wide pane; after that the user's toggle rules
  // (uncontrolled: a re-render never reopens it).
  const ref = useRef<HTMLDetailsElement>(null)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const pane = el.closest('[data-site-pane]') as HTMLElement | null
    el.open = !pane || pane.clientWidth === 0 || pane.clientWidth >= READOUT_OPEN_MIN_PX
  }, [showing])
  if (!showing) return null
  return (
    // Collapsible: it sits over the scene's top-right corner.
    <details ref={ref} className="max-h-[40vh] overflow-auto rounded-md border border-border bg-bg/95 px-2 py-1.5 text-[11px] shadow">
      <summary className="cursor-pointer select-none"><h3 className="inline font-semibold text-text">Results · {r.iso.replace('T', ' ').slice(0, 16)}</h3></summary>
      <ul className="mt-1 space-y-0.5 text-muted" aria-label="Site results">
        {[...visuals].map(([key, v]) => (
          <li key={key} className="flex items-center gap-1.5">
            {(v.color ?? v.fillColor) && <span aria-hidden className="inline-block h-2 w-2 shrink-0 rounded-sm" style={{ background: v.color ?? v.fillColor }} />}
            {v.label}
          </li>
        ))}
      </ul>
    </details>
  )
}
