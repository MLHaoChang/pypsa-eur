// "New 3D site" (Site 3D, part Map; opened by "Create 3D site"): the campus
// boundary as a rectangle about the confirmed site coordinates, set by
// numbers, then "Save site" saves an empty Site (buses: []) and adopts it as
// its 3D scene. The boundary is final at Save in this slice: no scene route
// edits it yet.
import { useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { openSiteAnchor } from '../layout/flowNavigation'
import { sceneErrorCopy } from '../api/siteScene'
import { evaluateMutation } from '../utils/mutationGuard'
import NumberField from './NumberField'
import { useSiteAnchor } from './SiteAnchorField'
import { useSiteCreation } from './siteCreation'
import { useSitesStore } from './sitesStore'
import { createSite, SitesNotSaved } from './sceneActions'
import { defaultSiteName, newSite } from './siteModel'
import { writeActiveSite } from './activeSite'
import { BOUNDARY_DEFAULTS, OFFSET_MAX_M, SIDE_MAX_M, SIDE_MIN_M, boundaryAround, boundaryErrors, rectangleLocal, type BoundaryInputs } from './nativeSite'

const REASON_NO_ANCHOR = 'Add site coordinates to create a 3D site'

/** A plan sketch: the rectangle, the coordinate marker and a scale bar. */
function PlanPreview({ v }: { v: BoundaryInputs }) {
  const ring = rectangleLocal({ x: v.east, y: v.north }, v.width, v.depth, v.heading)
  const reach = Math.max(...ring.map(p => Math.max(Math.abs(p.x), Math.abs(p.y))), 10) * 1.15
  const bar = [10, 20, 50, 100, 200, 500, 1000, 2000].find(b => b >= reach / 3) ?? 5000
  const s = 50 / reach
  const pts = ring.map(p => `${(p.x * s).toFixed(2)},${(-p.y * s).toFixed(2)}`).join(' ')
  return (
    <svg data-testid="site-create-preview" viewBox="-55 -55 110 110" className="h-32 w-32 rounded border border-border bg-bg" role="img"
      aria-label={`Boundary ${v.width} by ${v.depth} m about the site coordinates`}>
      <polygon points={pts} fill="var(--color-accent)" fillOpacity={0.12} stroke="var(--color-accent)" strokeWidth={1} />
      <circle cx={0} cy={0} r={2} fill="var(--color-text)" />
      <line x1={-50} y1={50} x2={-50 + bar * s} y2={50} stroke="var(--color-text)" strokeWidth={1} />
      <text x={-50} y={46} fontSize={7} fill="var(--color-muted)">{bar.toLocaleString('en-US')} m</text>
      <text x={0} y={-48} fontSize={7} textAnchor="middle" fill="var(--color-muted)">N</text>
    </svg>
  )
}

export default function SiteCreationPanel() {
  const project = useUIStore(s => s.currentProject)
  const readOnly = useUIStore(s => s.readOnly)
  const readOnlyReason = useUIStore(s => s.readOnlyReason)
  const doc = useSitesStore(s => s.docFor(project))
  const anchor = useSiteAnchor()
  const qc = useQueryClient()
  const [name, setName] = useState(() => defaultSiteName(doc))
  const [v, setV] = useState<BoundaryInputs>(BOUNDARY_DEFAULTS)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const errs = useMemo(() => boundaryErrors(v), [v])
  const set = (k: keyof BoundaryInputs) => (x: number) => setV(prev => ({ ...prev, [k]: x }))
  const a = anchor.data?.anchor
  const confirmed = a && a.confirmed ? a : null
  const guard = evaluateMutation(readOnly, readOnlyReason)
  const blocked = guard.allowed ? null : guard.blockedMessage
  const close = () => useSiteCreation.getState().setOpen(false)

  const save = async () => {
    if (!project || !confirmed || blocked || Object.keys(errs).length) return
    setBusy(true); setError(null)
    try {
      const site = newSite({ name: name.trim() || defaultSiteName(doc), boundary: boundaryAround(confirmed, v), buses: [] })
      await createSite(qc, project, site)
      const ui = useUIStore.getState()
      ui.setActiveSiteId(site.id)
      writeActiveSite(project, site.id)
      ui.setSitePart('generate')
      close()
    } catch (e) {
      setError(e instanceof SitesNotSaved ? e.message : sceneErrorCopy(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex h-full items-center justify-center px-6">
      <div role="dialog" aria-label="New 3D site" data-testid="site-create-panel"
        className="w-[min(34rem,100%)] rounded-lg border border-border bg-bg p-4 shadow-lg flex flex-col gap-3 text-[12px]">
        <div className="text-[11px] font-semibold uppercase tracking-wide text-muted">New 3D site</div>
        {!confirmed ? (
          <div data-testid="site-create-no-anchor" className="text-muted">
            {REASON_NO_ANCHOR}{' '}
            <button type="button" onClick={() => openSiteAnchor({ fromSite3D: true })} className="text-accent hover:underline">› Site coordinates</button>
          </div>
        ) : (
          <>
            <label className="flex flex-col gap-0.5 text-[11px]">
              <span className="text-muted">Name</span>
              <input type="text" value={name} maxLength={80} onChange={e => setName(e.target.value)} data-testid="site-create-name"
                className="w-60 rounded border border-border bg-bg px-2 py-1 text-[12px] text-text" />
            </label>
            <div className="flex flex-wrap gap-4">
              <div className="flex flex-col gap-2">
                <div className="text-muted">Boundary: a rectangle about the site coordinates</div>
                <div className="grid grid-cols-2 gap-x-3 gap-y-2">
                  <NumberField label="Width" unit="m" value={v.width} onChange={set('width')} error={errs.width} min={SIDE_MIN_M} max={SIDE_MAX_M} testId="site-create-width" width="w-24" />
                  <NumberField label="Depth" unit="m" value={v.depth} onChange={set('depth')} error={errs.depth} min={SIDE_MIN_M} max={SIDE_MAX_M} testId="site-create-depth" width="w-24" />
                  <NumberField label="Offset east" unit="m" value={v.east} onChange={set('east')} error={errs.east} min={-OFFSET_MAX_M} max={OFFSET_MAX_M} testId="site-create-east" width="w-24" />
                  <NumberField label="Offset north" unit="m" value={v.north} onChange={set('north')} error={errs.north} min={-OFFSET_MAX_M} max={OFFSET_MAX_M} testId="site-create-north" width="w-24" />
                  <NumberField label="Heading" unit="°" value={v.heading} onChange={set('heading')} error={errs.heading} hint="Clockwise from north" testId="site-create-heading" width="w-24" />
                </div>
                <div className="text-[10px] text-ink-400">
                  Sides {SIDE_MIN_M}–{SIDE_MAX_M.toLocaleString('en-US')} m · offset at most {OFFSET_MAX_M.toLocaleString('en-US')} m from the site coordinates
                </div>
              </div>
              <PlanPreview v={Object.keys(errs).length ? BOUNDARY_DEFAULTS : v} />
            </div>
            <div className="text-muted">The boundary is fixed once saved: buildings can still be corrected.</div>
            {blocked && <div data-testid="site-create-blocked" className="text-muted">{blocked}</div>}
            {error && <div role="alert" data-testid="site-create-error" className="text-danger">{error}</div>}
            <div className="flex gap-2">
              <button type="button" data-testid="site-create-save" onClick={() => void save()}
                disabled={busy || !!blocked || Object.keys(errs).length > 0}
                className="px-3 py-1 rounded bg-accent text-white font-semibold hover:bg-accent/90 disabled:opacity-50">
                {busy ? 'Saving…' : 'Save site'}
              </button>
              <button type="button" onClick={close} disabled={busy}
                className="px-3 py-1 rounded border border-border text-text hover:border-accent">Cancel</button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
