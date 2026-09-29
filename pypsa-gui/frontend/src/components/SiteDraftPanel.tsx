// The form that turns a drawn boundary into a site (or edits an existing
// site's name and buses). DOM only, so it is testable in jsdom; the map
// decides when to show it.
import { useMemo, useState } from 'react'
import { useUIStore } from '../store/uiStore'
import { useSitesStore } from '../site3d/sitesStore'
import { busesInside } from '../site3d/boundary'
import { defaultSiteName, newSite } from '../site3d/siteModel'
import { writeActiveSite } from '../site3d/activeSite'
import { readOnlyMessage, READ_ONLY_MUTATION_MESSAGE } from '../utils/mutationGuard'
import type { LngLatTuple, Site } from '../site3d/types'
import type { Bus } from '../api/types'

interface Props {
  /** The boundary just drawn (create) or the site's own (edit). */
  boundary: LngLatTuple[]
  /** Set to edit name and membership of an existing site. */
  existing?: Site | null
  buses: Bus[]
  onDone: (site: Site | null) => void
}

export default function SiteDraftPanel({ boundary, existing, buses, onDone }: Props) {
  const currentProject = useUIStore(s => s.currentProject)
  const readOnly = useUIStore(s => s.readOnly)
  const readOnlyReason = useUIStore(s => s.readOnlyReason)
  const setActiveSiteId = useUIStore(s => s.setActiveSiteId)
  const doc = useSitesStore(s => s.docFor(currentProject))
  const upsertSite = useSitesStore(s => s.upsertSite)

  const inside = useMemo(() => busesInside(buses, boundary), [buses, boundary])
  const [name, setName] = useState(existing?.name ?? defaultSiteName(doc))
  const [checked, setChecked] = useState<Set<string>>(() => new Set(existing ? existing.buses : inside))
  const placed = buses.filter(b => !(Number(b.x) === 0 && Number(b.y) === 0))

  const toggle = (n: string) => setChecked(prev => {
    const next = new Set(prev)
    if (next.has(n)) next.delete(n); else next.add(n)
    return next
  })

  const submit = () => {
    if (readOnly) return
    const trimmed = name.trim() || defaultSiteName(doc)
    // Keep the given order of buses (the first is the primary bus).
    const chosen = placed.map(b => b.name).filter(n => checked.has(n))
    const site: Site = existing
      ? { ...existing, name: trimmed, buses: chosen }
      : newSite({ name: trimmed, boundary, buses: chosen })
    upsertSite(currentProject, site)
    setActiveSiteId(site.id)
    writeActiveSite(currentProject, site.id)
    onDone(site)
  }

  const blocked = readOnly ? (readOnlyMessage(readOnlyReason) ?? READ_ONLY_MUTATION_MESSAGE) : undefined

  return (
    <div
      role="dialog"
      aria-label={existing ? 'Edit site' : 'New site'}
      className="absolute z-[900] left-3 top-14 w-64 rounded-lg border border-border bg-bg p-3 shadow-lg text-[12px]"
    >
      <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted">
        {existing ? 'Edit site' : 'New site'}
      </div>
      <label className="flex flex-col gap-1 mb-2">
        <span className="text-[11px] text-muted">Name</span>
        <input
          type="text"
          value={name}
          maxLength={80}
          onChange={e => setName(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') submit(); else if (e.key === 'Escape') onDone(null) }}
          className="bg-bg border border-border rounded px-1.5 py-0.5 text-[12px] text-text focus:outline-none focus:border-accent"
          autoFocus
        />
      </label>
      <div className="text-[11px] text-muted mb-1">
        Buses in this site <span className="text-muted/70">({inside.length} inside the boundary)</span>
      </div>
      <div className="max-h-40 overflow-y-auto flex flex-col gap-0.5 mb-3">
        {placed.length === 0 && <div className="text-muted italic">No placed buses.</div>}
        {placed.map(b => (
          <label key={b.name} className="flex items-center gap-2">
            <input type="checkbox" checked={checked.has(b.name)} onChange={() => toggle(b.name)} />
            <span className="font-mono text-text truncate">{b.name}</span>
            {inside.includes(b.name) && <span className="text-[10px] text-muted">inside</span>}
          </label>
        ))}
      </div>
      <div className="flex gap-2">
        <button
          type="button"
          onClick={submit}
          disabled={readOnly}
          title={blocked}
          className="flex-1 py-1 bg-accent text-white rounded text-[12px] font-semibold hover:bg-accent/90 disabled:opacity-50"
        >
          {existing ? 'Save' : 'Create'}
        </button>
        <button type="button" onClick={() => onDone(null)} className="px-3 py-1 border border-border rounded text-[12px] text-muted hover:text-text">
          Cancel
        </button>
      </div>
    </div>
  )
}
