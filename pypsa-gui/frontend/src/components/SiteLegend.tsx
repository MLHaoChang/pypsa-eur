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

const bandsOf = (r: SiteResults) => {
  let loading = false, soc = false
  for (const st of r.states.values()) { if (st.kind === 'branch') loading = true; else if (st.kind === 'storage') soc = true }
  return { loading, soc }
}
const sameBands = (a: { loading: boolean; soc: boolean }, b: { loading: boolean; soc: boolean }) => a.loading === b.loading && a.soc === b.soc

export default function SiteLegend({ entries, results }: Props) {
  // Each band only while results of its kind show (branches, storage).
  const bands = useResultsSelector(results, useCallback(bandsOf, []), sameBands)
  return (
    <div className="flex max-w-[min(100%,36rem)] flex-wrap gap-x-3 gap-y-1 rounded-md border border-border bg-bg/95 px-2 py-1.5 text-[11px] shadow">
      {entries.map(e => (
        <span key={e.id} className="flex items-center gap-1 text-muted"><Swatch color={e.color} />{e.label}</span>
      ))}
      {bands.loading && (
          <span data-testid="legend-loading" className="flex basis-full items-center gap-1 text-muted">
            Loading: <Swatch color={loadingColor(0)} /> &lt; 50 % <Swatch color={loadingColor(50)} /> 50–90 % <Swatch color={loadingColor(90)} /> ≥ 90 % of rating
          </span>
      )}
      {bands.soc && (
          <span data-testid="legend-soc" className="flex basis-full items-center gap-1 text-muted">
            SoC / fill: <Swatch color={socColor(0)} /> &lt; 20 % <Swatch color={socColor(20)} /> 20–80 % <Swatch color={socColor(80)} /> ≥ 80 %
          </span>
      )}
    </div>
  )
}
