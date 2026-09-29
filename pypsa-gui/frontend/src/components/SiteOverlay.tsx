// The 3D site view's control strip: site picker, counts, the fit check,
// Arrange / Reset placement. DOM only (testable in jsdom); the canvas
// supplies the numbers and the callbacks. Editing controls follow the
// app's read-only vocabulary: `disabled` + `title={readOnlyMessage(...)}`.
import { useUIStore } from '../store/uiStore'
import { readOnlyMessage, READ_ONLY_MUTATION_MESSAGE } from '../utils/mutationGuard'
import { formatHa, type FitReport } from '../site3d/fit'
import type { Site } from '../site3d/types'

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
}

export default function SiteOverlay({ sites, site, onPickSite, assetCount, fit, unplacedMembers, selectedPlacedKey, canArrange, onArrange, onResetPlacement }: Props) {
  const readOnly = useUIStore(s => s.readOnly)
  const readOnlyReason = useUIStore(s => s.readOnlyReason)
  const blocked = readOnly ? (readOnlyMessage(readOnlyReason) ?? READ_ONLY_MUTATION_MESSAGE) : undefined
  return (
    <div className="absolute left-3 top-12 z-[400] flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md border border-border bg-bg/95 px-2 py-1.5 text-[11px] shadow max-w-[calc(100%-24px)]">
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
        land {formatHa(fit.landM2)} · plot {formatHa(fit.plotM2)}{fit.over ? ' · does not fit' : ''}
      </span>
      {fit.outside.length > 0 && <span className="text-accent" title={fit.outside.join(', ')}>· {fit.outside.length} outside</span>}
      {unplacedMembers.length > 0 && (
        <span className="text-accent" title={unplacedMembers.join(', ')}>· {unplacedMembers.length} member bus{unplacedMembers.length === 1 ? '' : 'es'} not placed</span>
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
