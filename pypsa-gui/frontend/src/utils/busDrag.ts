// What a bus drag on the map commits (plan M5 pins it): the new position as
// bus.x = lng / bus.y = lat through the asset-write chokepoint (which
// spreads the cached row so the PUT carries the bus's full state), the
// length provenance the backend recorded mirrored into the map document,
// and the impedance-rescale previews offered through the shared store.
// Module-level so the map's mutation is a one-liner and the behaviour is
// testable without a Leaflet marker.
import type { QueryClient } from '@tanstack/react-query'
import type { Bus } from '../api/types'
import type { LengthSource } from '../api/mapLayout'
import { updateAsset } from './assetWrite'
import { ingestRescale } from './rescaleActions'
import type { RescalePreview } from './rescale'
import { useMapLayoutStore } from '../pages/mapLayoutStore'
import { appLog } from '../store/simulationStore'

export interface BusMoveResult {
  name?: string
  rescale?: RescalePreview[]
  length_sources?: Record<string, LengthSource>
}

/** `{ x: lng, y: lat }` — PyPSA's convention, the one swap the drag needs. */
export const busPositionPatch = (lat: number, lng: number): Pick<Bus, 'x' | 'y'> => ({ x: lng, y: lat })

export async function commitBusDrag(
  qc: QueryClient,
  project: string | null,
  name: string,
  lat: number,
  lng: number,
): Promise<BusMoveResult> {
  const resp = (await updateAsset<Bus>(qc, project, 'buses', name, busPositionPatch(lat, lng))) as BusMoveResult | null
  const result = resp ?? {}
  // The backend's update_bus already recomputed the lengths of THIS bus's
  // connected branches (lines by chord, or lines AND links from their
  // geometry with the project setting on) and logged a changelog entry;
  // the chokepoint's blanket invalidation covers the refetch.
  const sources = result.length_sources ?? {}
  const n = Object.keys(sources).length
  appLog('INFO', `Bus '${name}' moved · ${n} connected branch length${n === 1 ? '' : 's'} recalculated${
    Object.values(sources).includes('route') ? ' (routes followed)' : ''}.`)
  useMapLayoutStore.getState().applyLengthSources(project, result.length_sources)
  ingestRescale(qc, result.rescale ?? [])
  return result
}
