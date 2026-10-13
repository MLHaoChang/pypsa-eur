// What the 3D view shows when the project has no site yet (visual-layers
// plan 3 S4; owner decision O3): a readiness checklist computed from the
// model — a bus with coordinates, a branch between two buses, a boundary that
// exists or can be drawn — each unmet line a button to where it is fixed, and
// Generate 3D site once every line is met. Generate draws the default
// rectangle around the chosen bus, arranges everything by the placement rules
// (the layout with no placements, written as placements — Arrange all at
// birth) and makes the site active; the canvas then flies the camera in.
// DOM only; the canvas supplies the packed layout for a bus so the rectangle
// and the placements are computed here without three.
import { useState } from 'react'
import { useUIStore } from '../store/uiStore'
import { useSitesStore } from '../site3d/sitesStore'
import { defaultBoundaryFor } from '../site3d/boundary'
import { busOffsets, newSite } from '../site3d/siteModel'
import { writeActiveSite } from '../site3d/activeSite'
import { siteReadiness, type ReadinessFix } from '../site3d/readiness'
import { objectKey, type SiteLayout } from '../site3d/layout'
import { busLatLng } from '../utils/geo'
import { readOnlyMessage, READ_ONLY_MUTATION_MESSAGE } from '../utils/mutationGuard'
import type { Bus } from '../api/types'
import type { Placement } from '../site3d/types'

interface Props {
  buses: Bus[]
  /** Every two-port branch of the model (lines, links, transformers). */
  branches: readonly { bus0: string; bus1: string }[]
  /** The packed layout for a bus alone, in that bus's frame (metres from the bus). */
  layoutFor: (busName: string) => Pick<SiteLayout, 'bounds' | 'objects'>
  /** True while a component list is still loading: the checklist would read a half-loaded model. */
  loading?: boolean
}

export default function SiteEmptyState({ buses, branches, layoutFor, loading = false }: Props) {
  const currentProject = useUIStore(s => s.currentProject)
  const readOnly = useUIStore(s => s.readOnly)
  const readOnlyReason = useUIStore(s => s.readOnlyReason)
  const setActiveSiteId = useUIStore(s => s.setActiveSiteId)
  const setCanvasView = useUIStore(s => s.setCanvasView)
  const setCanvasMode = useUIStore(s => s.setCanvasMode)
  const upsertSite = useSitesStore(s => s.upsertSite)
  const readiness = siteReadiness({ buses, branches, sites: [] })
  const [busName, setBusName] = useState<string>(readiness.placedBuses[0] ?? '')

  const generate = () => {
    if (readOnly || !readiness.ready) return
    const bus = buses.find(b => b.name === busName && busLatLng(b) !== null) ?? buses.find(b => b.name === readiness.placedBuses[0])
    if (!bus) return
    const ll = busLatLng(bus)!
    const origin = { lng: ll[1], lat: ll[0] }
    const layout = layoutFor(bus.name)
    const boundary = defaultBoundaryFor(layout.bounds, origin)
    const site = newSite({ name: `${bus.name} site`, boundary, buses: [bus.name] })
    // Arrange by the rules at birth: the layout is in the bus's frame, a
    // placement in the site's (the boundary's centroid), hence the offset.
    const [dx, dy] = busOffsets(site, [bus])[bus.name] ?? [0, 0]
    const placements: Record<string, Placement> = {}
    for (const o of layout.objects) {
      if (o.elevation) continue   // on a roof: follows its hall
      placements[objectKey(o)] = { x: o.origin[0] + dx, y: o.origin[1] + dy, heading: o.heading }
    }
    upsertSite(currentProject, { ...site, placements })
    setActiveSiteId(site.id)
    writeActiveSite(currentProject, site.id)
  }

  const fix = (what: ReadinessFix) => {
    if (what === 'placeBus') setCanvasView('satellite')           // the Unplaced buses panel lives there
    else if (what === 'connect') { setCanvasMode('connect'); setCanvasView('blank') }
    else generate()
  }

  const blocked = readOnly ? (readOnlyMessage(readOnlyReason) ?? READ_ONLY_MUTATION_MESSAGE) : undefined

  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 text-center px-8" data-testid="site-empty-state">
      <div className="text-[13px] font-medium text-text">This project has no 3D site yet</div>
      <div className="text-[12px] text-muted max-w-md">
        The site is generated from the model's assets through the asset library, once the model has connections and coordinates.
      </div>
      {loading ? (
        <div className="text-[12px] text-muted">Checking the model…</div>
      ) : (
        <ul aria-label="Site readiness" className="flex flex-col gap-1.5 text-left text-[12px] max-w-md w-full">
          {readiness.lines.map(l => (
            <li key={l.id} data-testid={`readiness-${l.id}`} data-met={l.met} className="flex flex-wrap items-center gap-2">
              <span aria-hidden className={l.met ? 'text-success' : 'text-warn'}>{l.met ? '✓' : '!'}</span>
              <span className="sr-only">{l.met ? 'met:' : 'not met:'}</span>
              <span className="flex-1 min-w-[12rem] text-text">{l.text}</span>
              {!l.met && (
                <button
                  type="button"
                  onClick={() => fix(l.fix)}
                  className="px-2 py-0.5 rounded border border-border text-[11px] text-text hover:border-accent hover:text-accent"
                >
                  {l.fixLabel}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {!loading && readiness.ready && (
        <div className="flex items-center gap-2 text-[12px]">
          <label className="text-muted" htmlFor="site-generate-bus">around</label>
          <select id="site-generate-bus" aria-label="Bus for the new site" value={busName} onChange={e => setBusName(e.target.value)} className="bg-bg border border-border rounded px-1.5 py-1 text-text">
            {readiness.placedBuses.map(name => <option key={name} value={name}>{name}</option>)}
          </select>
          <button
            type="button"
            data-testid="generate-site"
            onClick={generate}
            disabled={readOnly}
            title={blocked ?? 'Draw a rectangle around everything attached to this bus, arrange it by the placement rules and open it'}
            className="px-3 py-1 bg-accent text-white rounded font-semibold hover:bg-accent/90 disabled:opacity-50"
          >
            Generate 3D site
          </button>
        </div>
      )}
      {!loading && readiness.ready && (
        <div className="text-[11px] text-muted max-w-md">
          Or draw any boundary on the Satellite map with <span className="font-medium">New site</span>.
        </div>
      )}
    </div>
  )
}
