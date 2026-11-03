import { client } from './client'

// SC-03 (D07): native results on a Site 3D scene's buildings and groups at one
// exact snapshot, `GET /projects/{name}/sites/{site_id}/scene/results`
// (`routers/site_scene.py`, `services/site_scene/results.py`). Read only: the
// GUI renders what the server projected and computes no number of its own.

/** One number: `{value: null, available: false, reason}` when unresolved; a genuine zero is `{value: 0, available: true}`. */
export interface ResultQuantity { value: number | null; available: boolean; reason: string | null; unit: string | null }

export type PortRole = 'input' | 'output' | 'storage' | 'unknown'
export interface ResultPort {
  port: string; bus: string; carrier: string | null; role: PortRole; reversed: boolean
  sign: 'withdrawal_positive' | 'injection_positive'
  rate: ResultQuantity
  rate_t?: ResultQuantity
  /** Only for an explicit share: the building's allocated part of `rate`. */
  allocated?: ResultQuantity
  window_energy?: ResultQuantity
  window_allocated?: ResultQuantity
}

export type ResultAllocation = null | { basis: 'explicit'; weight: number } | { basis: 'shared'; features: string[] }
export interface ResultTarget {
  class: string; uid: string | null; name: string
  allocation: ResultAllocation
  ports: ResultPort[]
  level?: ResultQuantity
  reason?: string
}

export interface GroupBoundaryRow { bus: string; carrier: string | null; net_import: ResultQuantity; window_energy?: ResultQuantity }
export interface GroupTotal { carrier: string | null; net_import: ResultQuantity; window_energy?: ResultQuantity }
export interface ResultGroup {
  boundary: GroupBoundaryRow[]
  internal_buses: string[]
  totals: GroupTotal[]
  total: ResultQuantity
  unavailable_member: boolean
}

export interface SceneResults {
  project: string
  site_id: string
  scene_revision: number
  run: { digest: string | null; current: boolean; reason: string | null }
  snapshot: {
    index: number; label: string | number; period: number | null
    weight_generators: ResultQuantity; period_years: ResultQuantity
  } | null
  window: { from: string | number; to: string | number; snapshots: number | null; basis: string } | null
  features: Record<string, { targets: ResultTarget[] }>
  groups: Record<string, ResultGroup>
}

/** The snapshot to ask for, as native labels: the label as `/network/snapshots` lists it, and its period when multi-period. */
export interface SceneResultsQuery { snapshot: string; period: string | null }

const enc = encodeURIComponent

export const siteSceneResultsApi = {
  get: (project: string, siteId: string, q: SceneResultsQuery) =>
    client.get<SceneResults>(`/projects/${enc(project)}/sites/${enc(siteId)}/scene/results`, {
      params: q.period == null ? { snapshot: q.snapshot } : { snapshot: q.snapshot, period: q.period },
      skipErrorToast: true,
    }).then(r => r.data),
}
