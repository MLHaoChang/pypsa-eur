// What the 3D view shows when the project has no site yet: the offer to
// create one around a placed bus. DOM only; the canvas supplies the packed
// bounds for a bus so the default rectangle is computed here without three.
import { useState } from 'react'
import { useUIStore } from '../store/uiStore'
import { useSitesStore } from '../site3d/sitesStore'
import { defaultBoundaryFor, type BoundsXY } from '../site3d/boundary'
import { newSite } from '../site3d/siteModel'
import { writeActiveSite } from '../site3d/activeSite'
import { busLatLng } from '../utils/geo'
import { readOnlyMessage, READ_ONLY_MUTATION_MESSAGE } from '../utils/mutationGuard'
import type { Bus } from '../api/types'

interface Props {
  buses: Bus[]
  /** The packed layout's bounds for a bus, metres from that bus. */
  boundsFor: (busName: string) => BoundsXY
}

export default function SiteEmptyState({ buses, boundsFor }: Props) {
  const currentProject = useUIStore(s => s.currentProject)
  const readOnly = useUIStore(s => s.readOnly)
  const readOnlyReason = useUIStore(s => s.readOnlyReason)
  const setActiveSiteId = useUIStore(s => s.setActiveSiteId)
  const upsertSite = useSitesStore(s => s.upsertSite)
  const placed = buses.filter(b => busLatLng(b) !== null)
  const [busName, setBusName] = useState<string>(placed[0]?.name ?? '')

  if (placed.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-center px-8">
        <div className="text-[13px] font-medium text-text">No placed bus to build a site from</div>
        <div className="text-[12px] text-muted max-w-md">
          A site is drawn around buses with coordinates. Switch to the Satellite view and place a bus on the map, then come back.
        </div>
      </div>
    )
  }

  const create = () => {
    if (readOnly) return
    const bus = placed.find(b => b.name === busName) ?? placed[0]
    const ll = busLatLng(bus)!
    const origin = { lng: ll[1], lat: ll[0] }
    const boundary = defaultBoundaryFor(boundsFor(bus.name), origin)
    const site = newSite({ name: `${bus.name} site`, boundary, buses: [bus.name] })
    upsertSite(currentProject, site)
    setActiveSiteId(site.id)
    writeActiveSite(currentProject, site.id)
  }

  const blocked = readOnly ? (readOnlyMessage(readOnlyReason) ?? READ_ONLY_MUTATION_MESSAGE) : undefined

  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 text-center px-8">
      <div className="text-[13px] font-medium text-text">This project has no site yet</div>
      <div className="text-[12px] text-muted max-w-md">
        Draw a boundary on the Satellite map with <span className="font-medium">New site</span>, or start from a bus: a rectangle around everything attached to it.
      </div>
      <div className="flex items-center gap-2 text-[12px]">
        <select aria-label="Bus for the new site" value={busName} onChange={e => setBusName(e.target.value)} className="bg-bg border border-border rounded px-1.5 py-1 text-text">
          {placed.map(b => <option key={b.name} value={b.name}>{b.name}</option>)}
        </select>
        <button
          type="button"
          onClick={create}
          disabled={readOnly}
          title={blocked}
          className="px-3 py-1 bg-accent text-white rounded font-semibold hover:bg-accent/90 disabled:opacity-50"
        >
          Create a site around this bus
        </button>
      </div>
    </div>
  )
}
