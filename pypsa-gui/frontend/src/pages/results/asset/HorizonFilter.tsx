import { Filter, RotateCcw } from 'lucide-react'
import type { ResultsFilterControls } from '../filterContext'

/**
 * Inline horizon control for the Asset Detail tab.
 *
 * The Results shell already has a horizon filter, but it is collapsed behind
 * a disclosure at the top of the panel — so a user reading an hourly series
 * had no visible way to narrow it to the week they cared about. This is the
 * same filter, not a second one: `controls` writes straight back to the
 * shell's state (see filterContext.ResultsFilterControls), so both stay in
 * lockstep and there is still one horizon for the whole Results panel.
 *
 * The period chips only render on a multi-period network, where `periods` is
 * non-empty.
 */
export default function HorizonFilter({ controls }: { controls: ResultsFilterControls }) {
  const {
    fromInput, toInput, setFromInput, setToInput,
    firstSnap, lastSnap, periods, selectedPeriod, setSelectedPeriod,
    isFiltered, reset,
  } = controls

  return (
    <div className={`shrink-0 flex items-center flex-wrap gap-x-2 gap-y-1 px-2 py-1.5
      border-b border-border ${isFiltered ? 'bg-warn/5' : ''}`}>
      <Filter size={11} className={isFiltered ? 'text-warn' : 'text-muted'} />
      <span className="text-[10px] uppercase tracking-wider text-muted">Horizon</span>

      <label className="flex items-center gap-1">
        <span className="sr-only">Horizon from</span>
        <input
          aria-label="Horizon from"
          type="datetime-local"
          value={fromInput}
          min={firstSnap || undefined}
          max={lastSnap || undefined}
          onChange={e => setFromInput(e.target.value)}
          className="px-1.5 py-0.5 border border-border rounded font-mono text-[11px] bg-bg"
        />
      </label>
      <span className="text-[10px] text-muted">→</span>
      <label className="flex items-center gap-1">
        <span className="sr-only">Horizon to</span>
        <input
          aria-label="Horizon to"
          type="datetime-local"
          value={toInput}
          min={firstSnap || undefined}
          max={lastSnap || undefined}
          onChange={e => setToInput(e.target.value)}
          className="px-1.5 py-0.5 border border-border rounded font-mono text-[11px] bg-bg"
        />
      </label>

      {isFiltered && (
        <button
          onClick={reset}
          title="Restore the full simulation horizon"
          className="flex items-center gap-1 px-1.5 py-0.5 text-[10px] text-muted hover:text-danger"
        ><RotateCcw size={10} /> Full horizon</button>
      )}

      {periods.length > 0 && (
        <>
          <span className="w-px h-4 bg-border mx-1" />
          <span className="text-[10px] uppercase tracking-wider text-muted">Period</span>
          <button
            onClick={() => setSelectedPeriod('all')}
            title="Aggregate every investment period, weight-scaled"
            className={`h-5 px-1.5 text-[10px] font-mono rounded
              ${selectedPeriod === 'all'
                ? 'bg-accent text-white'
                : 'text-muted hover:text-text border border-border'}`}
          >All</button>
          {periods.map(p => (
            <button
              key={String(p)}
              onClick={() => setSelectedPeriod(p)}
              title={`Show only investment period ${p}`}
              className={`h-5 px-1.5 text-[10px] font-mono rounded
                ${selectedPeriod === p
                  ? 'bg-accent text-white'
                  : 'text-muted hover:text-text border border-border'}`}
            >{String(p)}</button>
          ))}
        </>
      )}
    </div>
  )
}
