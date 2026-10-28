// "New 3D site" (Site 3D, part Map; opened by "Create 3D site"): the campus
// boundary as a rectangle about the confirmed site coordinates, set by
// numbers, then "Save site" saves an empty Site (buses: []) and adopts it as
// its 3D scene. The boundary is final at Save in this slice: no scene route
// edits it yet.
import { useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { openSiteAnchor, openSiteBoundaryDraw } from '../layout/flowNavigation'
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
const REASON_NO_NAME = 'Name the site to save it'
const SIDE_LIMIT = `${SIDE_MIN_M}–${SIDE_MAX_M.toLocaleString('en-US')} m`
const OFFSET_LIMIT = `At most ${OFFSET_MAX_M.toLocaleString('en-US')} m either way`
const FIELD_LIMITS: [keyof BoundaryInputs, string, string][] = [
  ['width', 'Width', SIDE_LIMIT], ['depth', 'Depth', SIDE_LIMIT],
  ['east', 'Offset east', OFFSET_LIMIT.toLowerCase()], ['north', 'Offset north', OFFSET_LIMIT.toLowerCase()],
  ['heading', 'Heading', 'a number of degrees'],
]

/** Why Save is off, as visible text (UX SC-01b addendum, item 1): the name first, then the first bad field and its limit. */
export function saveBlockedReason(name: string, errs: Partial<Record<keyof BoundaryInputs, string>>): string | null {
  if (!name.trim()) return REASON_NO_NAME
  const bad = FIELD_LIMITS.find(([k]) => errs[k])
  return bad ? `${bad[1]}: ${bad[2]}` : null
}

/** A plan sketch: the rectangle, the coordinate marker and a scale bar. */
function PlanPreview({ v, at }: { v: BoundaryInputs; at: { lat: number; lng: number } }) {
  const ring = rectangleLocal({ x: v.east, y: v.north }, v.width, v.depth, v.heading)
  const reach = Math.max(...ring.map(p => Math.max(Math.abs(p.x), Math.abs(p.y))), 10) * 1.3
  const bar = [10, 20, 50, 100, 200, 500, 1000, 2000].find(b => b >= reach / 3) ?? 5000
  const s = 50 / reach
  const pts = ring.map(p => `${(p.x * s).toFixed(2)},${(-p.y * s).toFixed(2)}`).join(' ')
  return (
    <svg data-testid="site-create-preview" viewBox="-55 -55 110 110" className="h-32 w-32 rounded border border-border bg-bg" role="img"
      aria-label={`Boundary ${v.width} by ${v.depth} m about the site coordinates`}>
      <polygon points={pts} fill="var(--color-accent)" fillOpacity={0.12} stroke="var(--color-accent)" strokeWidth={1} />
      <circle cx={0} cy={0} r={2} fill="var(--color-text)" />
      <text data-testid="site-create-marker" x={0} y={10} fontSize={7} textAnchor="middle" fill="var(--color-text)"
        stroke="var(--color-bg)" strokeWidth={2} paintOrder="stroke">
        {at.lat.toFixed(4)}, {at.lng.toFixed(4)}
      </text>
      <line x1={-50} y1={50} x2={-50 + bar * s} y2={50} stroke="var(--color-text)" strokeWidth={1} />
      <text x={-50} y={46} fontSize={7} fill="var(--color-muted)" stroke="var(--color-bg)" strokeWidth={2} paintOrder="stroke">{bar.toLocaleString('en-US')} m</text>
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
  const saveReason = saveBlockedReason(name, errs)

  const save = async () => {
    if (!project || !confirmed || blocked || saveReason) return
    setBusy(true); setError(null)
    try {
      const site = newSite({ name: name.trim(), boundary: boundaryAround(confirmed, v), buses: [] })
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
                  <NumberField label="Width" unit="m" value={v.width} onChange={set('width')} error={errs.width} hint={SIDE_LIMIT} min={SIDE_MIN_M} max={SIDE_MAX_M} testId="site-create-width" width="w-24" />
                  <NumberField label="Depth" unit="m" value={v.depth} onChange={set('depth')} error={errs.depth} hint={SIDE_LIMIT} min={SIDE_MIN_M} max={SIDE_MAX_M} testId="site-create-depth" width="w-24" />
                  <NumberField label="Offset east" unit="m" value={v.east} onChange={set('east')} error={errs.east} hint={OFFSET_LIMIT} min={-OFFSET_MAX_M} max={OFFSET_MAX_M} testId="site-create-east" width="w-24" />
                  <NumberField label="Offset north" unit="m" value={v.north} onChange={set('north')} error={errs.north} hint={OFFSET_LIMIT} min={-OFFSET_MAX_M} max={OFFSET_MAX_M} testId="site-create-north" width="w-24" />
                  <NumberField label="Heading" unit="°" value={v.heading} onChange={set('heading')} error={errs.heading} hint="Clockwise from north" testId="site-create-heading" width="w-24" />
                </div>
                <div className="text-[11px] text-muted" data-testid="site-create-draw">
                  Need another shape?{' '}
                  <button type="button" onClick={() => { close(); openSiteBoundaryDraw(confirmed) }} className="text-accent hover:underline">
                    Draw the boundary on the Satellite map ›
                  </button>
                </div>
              </div>
              <PlanPreview v={Object.keys(errs).length ? BOUNDARY_DEFAULTS : v} at={confirmed} />
            </div>
            <div className="text-muted">The boundary is fixed once saved: buildings can still be corrected.</div>
            {blocked && <div data-testid="site-create-blocked" className="text-muted">{blocked}</div>}
            {error && <div role="alert" data-testid="site-create-error" className="text-danger">{error}</div>}
            <div className="flex gap-2">
              <button type="button" data-testid="site-create-save" onClick={() => void save()}
                disabled={busy || !!blocked || !!saveReason}
                className="px-3 py-1 rounded bg-accent text-white font-semibold hover:bg-accent/90 disabled:opacity-50">
                {busy ? 'Saving…' : 'Save site'}
              </button>
              <button type="button" onClick={close} disabled={busy}
                className="px-3 py-1 rounded border border-border text-text hover:border-accent">Cancel</button>
              {saveReason && !blocked && <span data-testid="site-create-save-reason" className="self-center text-[11px] text-muted">{saveReason}</span>}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
