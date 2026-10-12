// The 3D site view's control strip: site picker, counts, the fit check,
// Arrange / Reset placement. DOM only (testable in jsdom); the canvas
// supplies the numbers and the callbacks, and positions the strip (it
// stacks the legend under it, so a wrapped strip never hides under the legend). Editing controls follow the
// app's read-only vocabulary: `disabled` + `title={readOnlyMessage(...)}`.
import { useUIStore } from '../store/uiStore'
import { readOnlyMessage, READ_ONLY_MUTATION_MESSAGE } from '../utils/mutationGuard'
import { formatHa, type FitReport } from '../site3d/fit'
import type { Site } from '../site3d/types'
import { effectiveSizing } from '../site3d/sizing'

interface Props {
  sites: Site[]
  site: Site
  onPickSite: (id: string) => void
  assetCount: number
  fit: FitReport
  unplacedMembers: string[]
  /** The selected object's placement key, if it has a placement (Reset applies to it). */
  selectedPlacedKey: string | null
  /** False while a component list is still loading: Arrange would write a partial site. */
  canArrange: boolean
  onArrange: () => void
  onResetPlacement: () => void
  /** The solver's dispatch matches the network: the as built / optimised switch is offered only then (spec E6). */
  dispatchFresh?: boolean
}

export default function SiteOverlay({ sites, site, onPickSite, assetCount, fit, unplacedMembers, selectedPlacedKey, canArrange, onArrange, onResetPlacement, dispatchFresh = false }: Props) {
  const readOnly = useUIStore(s => s.readOnly)
  const siteSizing = useUIStore(s => s.siteSizing)
  const setSiteSizing = useUIStore(s => s.setSiteSizing)
  const optimised = effectiveSizing(siteSizing, dispatchFresh) === 'optimised'
  const readOnlyReason = useUIStore(s => s.readOnlyReason)
  const blocked = readOnly ? (readOnlyMessage(readOnlyReason) ?? READ_ONLY_MUTATION_MESSAGE) : undefined
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md border border-border bg-bg/95 px-2 py-1.5 text-[11px] shadow max-w-full">
      <span className="text-muted">Site</span>
      <select className="bg-transparent text-text outline-none" value={site.id} onChange={e => onPickSite(e.target.value)} aria-label="Site">
        {sites.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
      </select>
      <span className="text-muted">·</span>
      <span className="text-muted">{assetCount} asset{assetCount === 1 ? '' : 's'}</span>
      <span className="text-muted">·</span>
      <span
        data-testid="fit-status"
        className={fit.over ? 'text-accent font-semibold' : 'text-muted'}
        title="Sum of every asset's land take from its parameters, against the plot area inside the boundary"
      >
        land {formatHa(fit.landM2)}{optimised ? ' (optimised)' : ''} · plot {formatHa(fit.plotM2)}{fit.over ? ' · does not fit' : ''}
      </span>
      {fit.outside.length > 0 && <span className="text-accent" title={fit.outside.join(', ')}>· {fit.outside.length} outside</span>}
      {unplacedMembers.length > 0 && (
        <span className="text-accent" title={unplacedMembers.join(', ')}>· {unplacedMembers.length} member bus{unplacedMembers.length === 1 ? '' : 'es'} not placed</span>
      )}
      {dispatchFresh && (
        // A view setting: enabled when read-only too.
        <span className="inline-flex items-center gap-1" role="group" aria-label="Sized">
          <span className="text-muted">· Sized:</span>
          {(['installed', 'optimised'] as const).map(m => (
            <button
              key={m}
              type="button"
              aria-pressed={siteSizing === m}
              onClick={() => setSiteSizing(m)}
              title={m === 'installed' ? 'Draw every asset at its installed size (p_nom, e_nom, s_nom)' : "Draw extendable assets at the last solve's optimum (*_nom_opt)"}
              className={`px-1.5 py-0.5 rounded border border-border ${siteSizing === m ? 'bg-accent/10 text-text font-semibold' : 'text-muted hover:bg-accent/5'}`}
            >
              {m === 'installed' ? 'as built' : 'optimised'}
            </button>
          ))}
        </span>
      )}
      <span className="text-muted">·</span>
      <button
        type="button"
        onClick={onArrange}
        disabled={readOnly || !canArrange}
        title={blocked ?? (canArrange ? 'Write every packed position as a placement so it stays put' : 'Waiting for the network to load')}
        className="px-1.5 py-0.5 rounded border border-border text-text hover:bg-accent/5 disabled:opacity-50"
      >
        Arrange
      </button>
      <button
        type="button"
        onClick={onResetPlacement}
        disabled={readOnly || !selectedPlacedKey}
        title={blocked ?? (selectedPlacedKey ? `Reset ${selectedPlacedKey} to its packed position` : 'Select a placed asset to reset it')}
        className="px-1.5 py-0.5 rounded border border-border text-text hover:bg-accent/5 disabled:opacity-50"
      >
        Reset placement
      </button>
    </div>
  )
}
