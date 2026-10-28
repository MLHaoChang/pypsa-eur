import axios from 'axios'
import { client } from './client'

// The Site 3D scene routes (SC-01a, `routers/site_scene.py`): the project's
// site anchor (compare-and-swap on `expected_revision`), a Site's scene, its
// explicit adoption and the in-memory proposals (create → review → apply or
// cancel). Every call is quiet: the caller shows `sceneErrorCopy(err)` where
// the action is, never the server's own message (UX, #807 review).

export interface SiteAnchor { lat: number; lng: number; source: 'manual' | 'geocode'; confirmed: boolean }
export interface SiteAnchorDoc { version: 1; revision: number; anchor: SiteAnchor | null }

export type HeightEvidence = 'tagged' | 'levels' | 'fallback' | 'greenfield'

/** A native building (native.py, frozen): footprint in Site-local metres, counter-clockwise. */
export interface SceneBuilding {
  id: string
  source: { namespace: 'osm' | 'greenfield' | 'manual' | 'layout'; source_id: string }
  footprint: [number, number][]
  height_m: number
  height_evidence: HeightEvidence
  levels: number | null
}

export interface SceneReceipt { proposal_id: string; revision: number; [k: string]: unknown }

export type SiteScene =
  | { state: 'unadopted'; site_id: string; digest: string }
  | {
      state: 'enabled'
      site_id: string
      revision: number
      generation: string
      site: Record<string, unknown>
      buildings: SceneBuilding[]
      asset_footprints: Record<string, unknown>
      last_receipt: SceneReceipt | null
      /** Keys whose component is gone from the open network; null when not checked. */
      unbound_keys: string[] | null
    }

/** A proposed rectangle in Site-local metres (`native.greenfield_building`). */
export interface GreenfieldInput {
  id: string
  center: [number, number]
  width: number
  depth: number
  height: number
  heading: number
  unit: 'm'
}

/** A mapped footprint (`native.source_building`): WGS84 ring, optional height or levels. */
export interface SourceInput {
  namespace: 'osm' | 'manual'
  source_id: string
  ring: [number, number][]
  height_m?: number
  levels?: number
}

export interface ProposalInputs { greenfield?: GreenfieldInput[]; sources?: SourceInput[] }
export type ProposalAction = 'generate_native' | 'edit_buildings'

export interface Proposal {
  id: string
  site_id: string
  action: ProposalAction
  base_revision: number
  input_hash: string
  status: string
  preview: { buildings: SceneBuilding[]; stats: { buildings: number; triangles: number; bytes: number } }
}

const enc = encodeURIComponent
const anchorPath = (p: string) => `/projects/${enc(p)}/site-anchor`
const scenePath = (p: string, site: string) => `/projects/${enc(p)}/sites/${enc(site)}/scene`
const quiet = { skipErrorToast: true }

export const siteSceneApi = {
  getAnchor: (project: string) =>
    client.get<SiteAnchorDoc>(anchorPath(project), quiet).then(r => r.data),
  putAnchor: (project: string, anchor: SiteAnchor | null, expectedRevision: number) =>
    client.put<SiteAnchorDoc>(anchorPath(project), { anchor, expected_revision: expectedRevision }, quiet).then(r => r.data),
  getScene: (project: string, siteId: string) =>
    client.get<SiteScene>(scenePath(project, siteId), quiet).then(r => r.data),
  adopt: (project: string, siteId: string, expectedDigest: string) =>
    client.post<SiteScene>(`${scenePath(project, siteId)}/adopt`, { expected_digest: expectedDigest }, quiet).then(r => r.data),
  propose: (project: string, siteId: string, action: ProposalAction, baseRevision: number, inputs: ProposalInputs) =>
    client.post<Proposal>(`${scenePath(project, siteId)}/proposals`, { action, base_revision: baseRevision, inputs }, quiet).then(r => r.data),
  apply: (project: string, siteId: string, proposal: Pick<Proposal, 'id' | 'base_revision' | 'input_hash'>) =>
    client.post<SceneReceipt>(`${scenePath(project, siteId)}/proposals/${enc(proposal.id)}/apply`,
      { expected_revision: proposal.base_revision, input_hash: proposal.input_hash }, quiet).then(r => r.data),
  cancel: (project: string, siteId: string, proposalId: string) =>
    client.post<unknown>(`${scenePath(project, siteId)}/proposals/${enc(proposalId)}/cancel`, undefined, quiet).then(r => r.data),
}

// ── error copy ──────────────────────────────────────────────────────────────

export interface SceneErrorDetail {
  error_kind: string
  message?: string
  field?: string
  hint?: string
  limit?: string
  max?: number
  actual?: number
  current_revision?: number
  site_id?: string
}

/** The `{detail: {error_kind, …}}` body of a refused scene call, or null (network error, other shape). */
export function sceneErrorDetail(err: unknown): SceneErrorDetail | null {
  if (!axios.isAxiosError(err)) return null
  const d = (err.response?.data as { detail?: unknown } | undefined)?.detail
  return d && typeof d === 'object' && typeof (d as SceneErrorDetail).error_kind === 'string' ? d as SceneErrorDetail : null
}

const FIELD_WORDS: Record<string, string> = {
  lat: 'Latitude', lng: 'Longitude', source: 'The coordinate source', confirmed: 'Confirmation',
  expected_revision: 'The revision', expected_digest: 'The site version', boundary: 'The boundary',
  action: 'The action', base_revision: 'The revision', inputs: 'The building inputs',
}

const LIMIT_WORDS: Record<string, string> = {
  buildings: 'buildings', footprint_vertices: 'footprint corners', segment_pairs: 'edge pairs to check',
  triangles: 'triangles',
}

const BYTE_LIMITS: Record<string, string> = { preview_bytes: 'the preview', request_bytes: 'the request' }
const mb = (v: number) => `${(v / 1_000_000).toFixed(1)} MB`

const n = (v: number) => v.toLocaleString('en-US')

/** The geometry refusal's subject ("Building 3", "Mapped building 2") and its rule, rebuilt as copy. */
function geometryCopy(message: string): string {
  const who = /^(Mapped building|Building) (\d+)/.exec(message)
  const subject = who ? `${who[1]} ${who[2]}` : 'A building'
  if (message.includes('crosses itself')) return `${subject}: the footprint crosses itself`
  if (message.includes('no area')) return `${subject}: the footprint has no area`
  if (message.includes('three vertices')) return `${subject}: a footprint needs at least three corners`
  if (message.includes('height')) return `${subject}: height must be above 0 m and at most 1000 m`
  return `${subject}: the footprint is not valid`
}

function invalidInputCopy(d: SceneErrorDetail): string {
  if (d.field === 'lat' && d.hint === 'swapped') return 'Latitude must be between −90 and 90: did you swap latitude and longitude?'
  if (d.field === 'lat') return 'Latitude: enter a number between −90 and 90'
  if (d.field === 'lng') return 'Longitude: enter a number between −180 and 180'
  if (d.field === 'boundary') return 'No campus boundary: draw it to generate buildings'
  const msg = d.message ?? ''
  if (msg.includes('heading')) return geometryCopy(msg).replace(/:.*$/, ': heading must be a number')
  if (msg.includes('duplicate')) return 'Two buildings share an id: remove the duplicate'
  if (msg.includes('namespace')) return geometryCopy(msg).replace(/:.*$/, ': its source is not recognised')
  const field = d.field ? FIELD_WORDS[d.field] : undefined
  return field ? `${field} is not valid: check it and try again` : 'The request was not valid: check the values and try again'
}

/**
 * UI copy for a refused scene call: built from `error_kind` and the detail
 * fields (field, hint, limit, max, actual), never the server's message as is.
 */
export function sceneErrorCopy(err: unknown): string {
  const d = sceneErrorDetail(err)
  if (!d) {
    if (axios.isAxiosError(err) && !err.response) return 'The server did not answer: check the connection and try again'
    return 'Something went wrong: try again'
  }
  switch (d.error_kind) {
    case 'invalid_input': return invalidInputCopy(d)
    case 'invalid_geometry': return geometryCopy(d.message ?? '')
    case 'revision_conflict': return 'Changed elsewhere since you opened it: reload to see the latest'
    case 'project_locked': return 'Another user is editing this project: try again when they finish'
    case 'scene_managed_site': return 'This site is managed by its 3D scene: reload to edit it'
    case 'proposal_expired': return 'This preview expired: generate again'
    case 'proposal_cancelled': return 'This preview was discarded: generate again'
    case 'resource_limit': {
      const whole = d.limit ? BYTE_LIMITS[d.limit] : undefined
      if (whole && d.max !== undefined && d.actual !== undefined) return `Too large: ${whole} is ${mb(d.actual)}, at most ${mb(d.max)}`
      const what = (d.limit && LIMIT_WORDS[d.limit]) || 'items'
      return d.max !== undefined && d.actual !== undefined
        ? `Too large: ${n(d.actual)} ${what}, at most ${n(d.max)}`
        : `Too large: too many ${what}`
    }
    case 'not_found': return 'This site no longer exists: reload'
    case 'forbidden': return 'You cannot change this project: ask an editor'
    case 'integrity_error': return 'This site’s 3D scene could not be read: restore a snapshot of the project'
    default: return 'Something went wrong: try again'
  }
}

/** Kinds a reload resolves: the caller offers Reload beside the copy. */
export const sceneErrorWantsReload = (err: unknown): boolean =>
  ['revision_conflict', 'scene_managed_site', 'not_found'].includes(sceneErrorDetail(err)?.error_kind ?? '')
