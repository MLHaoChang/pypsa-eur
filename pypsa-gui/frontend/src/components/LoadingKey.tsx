import { LOADING_BANDS, NO_RESULT_COLOR, useCanvasResults } from './CanvasResultsContext'

// The key for the results overlay's loading colours, shared by the schematic
// legend and the map legend. With the overlay on, lines, links and
// transformers are coloured by loading instead of voltage or carrier, so the
// key shows only then.
export default function LoadingKey() {
  const results = useCanvasResults()
  if (!results.enabled) return null
  return (
    <div data-testid="legend-loading">
      <p className="text-[9px] font-bold text-muted uppercase tracking-[0.14em] mb-2 mt-3">Loading (% of rating)</p>
      {[...LOADING_BANDS].reverse().map(b => (
        <div key={b.label} className="flex items-center gap-2 mb-1">
          <svg width="20" height="3" style={{ flexShrink: 0 }}><line x1="0" y1="1.5" x2="20" y2="1.5" stroke={b.color} strokeWidth="2.5" strokeLinecap="round" /></svg>
          <span className="text-[10.5px] text-ink-700">{b.label}</span>
        </div>
      ))}
      <div className="flex items-center gap-2 mb-1">
        <svg width="20" height="3" style={{ flexShrink: 0 }}><line x1="0" y1="1.5" x2="20" y2="1.5" stroke={NO_RESULT_COLOR} strokeWidth="2.5" strokeLinecap="round" /></svg>
        <span className="text-[10.5px] text-ink-700">No result</span>
      </div>
    </div>
  )
}

/**
 * The voltage key's heading. With the overlay on the lines are coloured by
 * loading, so the voltage colours then describe the buses only.
 */
export function VoltageKeyTitle() {
  return <>{useCanvasResults().enabled ? 'Buses · voltage' : 'Buses & Lines · voltage'}</>
}
