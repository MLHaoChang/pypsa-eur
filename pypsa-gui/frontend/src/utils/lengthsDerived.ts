// What a lengths-from-geometry result does to the app (plan M2's endpoint,
// `POST /api/network/lengths/from_geometry`): the Line and Link lists are
// stale (their lengths changed), the provenance the server recorded is
// mirrored into the map document so the next PUT carries it, the log says
// what happened, and the impedance-rescale previews go to the one dialog
// host (`store/rescaleStore.ts` via `ingestRescale`). MapCanvas does the
// same inline for the map's writes; the 3D site view (S3) uses this for its
// route writes, so the two surfaces treat a derivation alike.
import type { QueryClient } from '@tanstack/react-query'
import type { LengthsFromGeometryResult } from '../api/network'
import { useMapLayoutStore } from '../pages/mapLayoutStore'
import { appLog } from '../store/simulationStore'
import { useUIStore } from '../store/uiStore'
import { nk } from './queryKeys'
import { ingestRescale } from './rescaleActions'

export function applyLengthsDerived(qc: QueryClient, r: LengthsFromGeometryResult, origin = 'map geometry'): void {
  const project = useUIStore.getState().currentProject
  qc.invalidateQueries({ queryKey: nk(project, 'lines') })
  qc.invalidateQueries({ queryKey: nk(project, 'links') })
  useMapLayoutStore.getState().applyLengthSources(project, r.sources)
  if (r.updated) {
    appLog('INFO', `${r.updated} branch length${r.updated === 1 ? '' : 's'} derived from ${origin} (${
      Object.values(r.sources).filter(s => s === 'route').length} routed).`)
  }
  ingestRescale(qc, r.rescale)
}
