// The create forms' optional site location (P5, TEMPLATE-GAPS-1 Q1 (a)): the
// DC and Generic Site forms ask for it, and once the project exists the form
// writes it as the site anchor, the record the Guided "Open here" drawer edits.
// Blank skips it; a half-filled pair or an out-of-range value is checked here.
import { networkApi } from '../../api/network'
import { useUIStore } from '../../store/uiStore'
import { siteSceneApi } from '../../api/siteScene'
import { anchorInputErrors } from '../../site3d/SiteAnchorField'

export interface LocationDraft { lat: string; lng: string }
export const emptyLocation = (): LocationDraft => ({ lat: '', lng: '' })

export type LocationErrors = { lat?: string; lng?: string }

/** Blank pair: fine (skipped). One blank: say which. Both set: the drawer's checks. */
export function validateLocation(d: LocationDraft): LocationErrors {
  const latBlank = d.lat.trim() === '', lngBlank = d.lng.trim() === ''
  if (latBlank && lngBlank) return {}
  const e: LocationErrors = {}
  if (latBlank) e.lat = 'Latitude: enter a number between −90 and 90, or clear longitude to skip'
  if (lngBlank) e.lng = 'Longitude: enter a number between −180 and 180, or clear latitude to skip'
  if (latBlank || lngBlank) return e
  const c = anchorInputErrors(Number(d.lat), Number(d.lng))
  if (c.lat) e.lat = c.lat
  if (c.lng) e.lng = c.lng
  return e
}

/** The pair as numbers, or null when it is skipped or invalid. */
export function locationOf(d: LocationDraft): { lat: number; lng: number } | null {
  if (d.lat.trim() === '' || d.lng.trim() === '') return null
  const lat = Number(d.lat), lng = Number(d.lng)
  const c = anchorInputErrors(lat, lng)
  return c.lat || c.lng ? null : { lat, lng }
}

/** Resolves once this tab's project is `project` and no mismatch is set; false
 *  on timeout. The new project becomes current as the create finishes, which
 *  can lag a slow open. */
function projectOpen(project: string, timeoutMs = 20000): Promise<boolean> {
  const ready = () => { const u = useUIStore.getState(); return u.currentProject === project && !u.projectMismatch }
  if (ready()) return Promise.resolve(true)
  return new Promise(resolve => {
    const done = (v: boolean) => { clearTimeout(timer); unsub(); resolve(v) }
    const unsub = useUIStore.subscribe(() => { if (ready()) done(true) })
    const timer = setTimeout(() => done(false), timeoutMs)
  })
}

/** Write the entered location as the new project's confirmed site anchor and
 *  put its buses there (the readiness note "The site has no location" reads
 *  the buses' x/y, not the anchor). Does nothing for a skipped pair. Returns an error text, or null when written. */
export async function writeCreatedLocation(project: string, d: LocationDraft): Promise<string | null> {
  const loc = locationOf(d)
  if (!loc) return null
  try {
    const doc = await siteSceneApi.getAnchor(project)
    await siteSceneApi.putAnchor(project, { ...loc, source: 'manual', confirmed: true }, doc.revision)
    // The bus calls name no project: they act on the live network. Write only
    // while this tab's project is the new one and the backend agrees.
    if (!(await projectOpen(project))) throw new Error('another project is open')
    const buses = await networkApi.getBuses()
    if (buses.length) {
      await networkApi.bulkUpdate({ component_class: 'Bus', names: buses.map(b => b.name),
                                    updates: { x: loc.lng, y: loc.lat } })
    }
    return null
  } catch {
    return 'The project was created, but its location was not saved.'
  }
}
