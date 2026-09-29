// The 3D view's legend (Phase 2 plan Task 6.3): the asset types drawn, and —
// only while results show — the loading and SoC bands with their thresholds
// as numbers (never colour only). DOM, no three; it subscribes to "are
// results showing" itself, so a snapshot step does not re-render it.
import { useCallback } from 'react'
import { loadingColor, socColor } from './CanvasResultsContext'
import { useResultsSelector, type ResultsStore } from '../site3d/resultsStore'
import type { SiteResults } from '../site3d/useSiteResults'

interface Props {
  entries: readonly { id: string; label: string; color: string }[]
  results: ResultsStore
}

const Swatch = ({ color }: { color: string }) => <span aria-hidden className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: color }} />

export default function SiteLegend({ entries, results }: Props) {
  const showing = useResultsSelector(results, useCallback((r: SiteResults) => r.states.size > 0, []))
  return (
    <div className="flex max-w-[min(100%,36rem)] flex-wrap gap-x-3 gap-y-1 rounded-md border border-border bg-bg/95 px-2 py-1.5 text-[11px] shadow">
      {entries.map(e => (
        <span key={e.id} className="flex items-center gap-1 text-muted"><Swatch color={e.color} />{e.label}</span>
      ))}
      {showing && (
        <>
          <span data-testid="legend-loading" className="flex basis-full items-center gap-1 text-muted">
            Loading: <Swatch color={loadingColor(0)} /> &lt; 50 % <Swatch color={loadingColor(50)} /> 50–90 % <Swatch color={loadingColor(90)} /> ≥ 90 % of rating
          </span>
          <span data-testid="legend-soc" className="flex basis-full items-center gap-1 text-muted">
            SoC / fill: <Swatch color={socColor(0)} /> &lt; 20 % <Swatch color={socColor(20)} /> 20–80 % <Swatch color={socColor(80)} /> ≥ 80 %
          </span>
        </>
      )}
    </div>
  )
}
