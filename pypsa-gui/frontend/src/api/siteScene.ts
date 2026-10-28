import axios from 'axios'
import { client } from './client'

// The Site 3D scene routes (SC-01a, `routers/site_scene.py`): the project's
// site anchor (compare-and-swap on `expected_revision`), a Site's scene, its
// explicit adoption and the in-memory proposals (create → review → apply or
// cancel). Every call is quiet: the caller shows `sceneErrorCopy(err)` where
// the action is.

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
      /** SC-02a read-back: each association with its live name and status. */
      associations?: SceneAssociation[]
      /** One summary per bound instance (capacity counted once). */
      targets?: SceneTarget[]
      groups?: SceneGroup[]
    }

// ── SC-02a associations (frozen contract, `services/site_scene/associations.py`) ──

/** The native classes a scene feature can be attached to. */
export type TargetClass = 'Load' | 'Link' | 'Store' | 'Generator' | 'StorageUnit'
export type AssociationStatus = 'bound' | 'unbound' | 'unavailable' | 'not_checked'

/** contracts.md §6: a number that may be missing, with its reason. */
export interface SceneQuantity { value: number | null; available: boolean; reason: string | null; unit: string }

export interface SceneAssociation {
  id: string
  /** A building id, a group id or a placement key. */
  feature: string
  class: TargetClass
  uid: string | null
  /** The live name; null unless bound. */
  name: string | null
  name_at_bind: string | null
  role: string
  weight?: number | null
  carrier: string | null
  unit: string | null
  status: AssociationStatus
  reason: string | null
  rebound_from: string | null
}

export interface SceneTarget {
  class: TargetClass
  name: string
  uid?: string
  features: string[]
  enclosures: number
  capacity: SceneQuantity
  allocation: { basis: 'none' | 'explicit'; shares: { feature: string; weight: number; allocated: SceneQuantity }[] }
}

export interface SceneGroup { id: string; label: string; members: string[] }

/** A target by name, or (a Load only) by carrier. */
export type BindTarget = { class: TargetClass; name: string } | { class: 'Load'; carrier: string }
/** `{id}` keeps an existing association as it is. */
export type BindRow = { id: string } | { feature: string; target: BindTarget; weight?: number }
export interface BindInputs { associations: BindRow[]; groups?: SceneGroup[] }
export interface RebindInputs {
  association?: string
  placement?: string
  target: { class: string; name: string }
  confirmed: true
}
export type PlaceOn = 'ground' | `roof:${string}`
export interface PlaceEntry { x: number; y: number; heading: number; on?: PlaceOn }
export interface PlaceInputs { placements: Record<string, PlaceEntry> }

/** A non-blocking finding on a preview: `double_counted_demand`, `overlap`. */
export interface SceneFinding { code: string; severity: 'warn' | 'info'; message: string; objects?: string[]; feature?: string }

export interface BindPreviewRow {
  feature: string
  class: TargetClass
  name: string | null
  carrier: string | null
  unit: string | null
  role: string | null
  weight: number | null
  status: 'new' | 'kept' | 'unavailable'
  reason: string | null
}
export interface BindPreview { associations: BindPreviewRow[]; groups: SceneGroup[]; findings: SceneFinding[] }
export interface RebindPreview {
  association?: string
  placement?: string
  feature?: string
  class: string
  name: string
  was: string | null
  findings: SceneFinding[]
}
export interface PlacePreview { placements: Record<string, PlaceEntry>; findings: SceneFinding[] }

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

export interface BuildingInputs { greenfield?: GreenfieldInput[]; sources?: SourceInput[] }
export type ProposalInputs = BuildingInputs | BindInputs | RebindInputs | PlaceInputs
export type ProposalAction = 'generate_native' | 'edit_buildings' | 'bind_targets' | 'rebind' | 'place_existing'

export interface BuildingsPreview { buildings: SceneBuilding[]; stats: { buildings: number; triangles: number; bytes: number } }

/** A proposal under review; the preview's shape follows its action (buildings by default). */
export interface Proposal<P = BuildingsPreview> {
  id: string
  site_id: string
  action: ProposalAction
  base_revision: number
  input_hash: string
  status: string
  preview: P
}

/** The SC-02 proposals the Model part reviews. */
export type ModelProposal =
  | (Proposal<BindPreview> & { action: 'bind_targets' })
  | (Proposal<RebindPreview> & { action: 'rebind' })
  | (Proposal<PlacePreview> & { action: 'place_existing' })

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
  propose: <P = BuildingsPreview>(project: string, siteId: string, action: ProposalAction, baseRevision: number, inputs: ProposalInputs) =>
    client.post<Proposal<P>>(`${scenePath(project, siteId)}/proposals`, { action, base_revision: baseRevision, inputs }, quiet).then(r => r.data),
  apply: (project: string, siteId: string, proposal: Pick<Proposal<unknown>, 'id' | 'base_revision' | 'input_hash'>) =>
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
  /** SC-02a route detail code under invalid_input / revision_conflict. */
  code?: string
  /** ambiguous_target: the names the user picks from (never picked for them). */
  candidates?: string[]
  carrier?: string
  key?: string
  path?: string[]
  target?: { class: string; name: string }
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

/** A Site the 3D scene owns: its boundary and placements change only through the scene (UX #821). */
export const MANAGED_SITE_COPY = 'This site’s boundary and equipment positions are managed by its 3D scene: correct buildings in Generate'

/** The refused building's name ("Building 3", "Mapped building 2") at the start of a server message. */
function subjectOf(message: string): string {
  const who = /^(Mapped building|Building) (\d+)/.exec(message)
  return who ? `${who[1]} ${who[2]}` : 'A building'
}

function invalidInputCopy(d: SceneErrorDetail): string {
  if (d.field === 'lat' && d.hint === 'swapped') return 'Latitude must be between −90 and 90: did you swap latitude and longitude?'
  if (d.field === 'lat') return 'Latitude: enter a number between −90 and 90'
  if (d.field === 'lng') return 'Longitude: enter a number between −180 and 180'
  if (d.field === 'boundary') return 'No campus boundary: draw it to generate buildings'
  const msg = d.message ?? ''
  if (msg.includes('heading')) return `${subjectOf(msg)}: heading must be a number`
  if (msg.includes('duplicate')) return 'Two buildings share an id: remove the duplicate'
  if (msg.includes('namespace')) return `${subjectOf(msg)}: its source is not recognised`
  const field = d.field ? FIELD_WORDS[d.field] : undefined
  return field ? `${field} is not valid: check it and try again` : 'The request was not valid: check the values and try again'
}

/** SC-02a route detail codes whose server message already names things in words. */
export const MODEL_CODES = new Set([
  'ambiguous_target', 'invalid_allocation', 'confirmation_required', 'unbound_target', 'not_roof_eligible', 'off_roof',
  'project_not_open', 'target_changed', 'unknown_target', 'unsupported_target', 'orphan_member', 'cycle', 'duplicate',
  'invalid_target', 'invalid_groups', 'invalid_associations', 'invalid_placements', 'unknown_association',
])

/** A server message in house style: a colon, not an em dash; no Python quotes. */
export function plainMessage(message: string): string {
  return message
    .replace(/\s+[—–]\s+/g, ': ')
    .replace(/(^|[\s(:])'([^']+)'(?=[\s).,:;]|$)/g, '$1$2')
}

function modelCodeCopy(d: SceneErrorDetail): string {
  if (d.code === 'project_not_open') return d.message ? plainMessage(d.message) : 'Open the project’s model to check attachments'
  if (d.code === 'ambiguous_target') return d.message ? plainMessage(d.message) : 'Several items match: choose one'
  return d.message ? plainMessage(d.message) : 'The request was not valid: check the values and try again'
}

/**
 * UI copy for a refused scene call. The server's own text shows as is for
 * invalid_geometry, resource_limit and the proposal kinds (UX SC-01b plan,
 * condition 2); the other kinds map from `error_kind` and the detail fields.
 */
export function sceneErrorCopy(err: unknown): string {
  const d = sceneErrorDetail(err)
  if (!d) {
    if (axios.isAxiosError(err) && !err.response) return 'The server did not answer: check the connection and try again'
    return 'Something went wrong: try again'
  }
  if (d.code && MODEL_CODES.has(d.code) && ['invalid_input', 'revision_conflict'].includes(d.error_kind)) return modelCodeCopy(d)
  switch (d.error_kind) {
    case 'invalid_input': return invalidInputCopy(d)
    case 'invalid_geometry': return d.message || 'A building footprint is not valid'
    case 'revision_conflict': return d.message || 'Changed elsewhere since you opened it: reload to see the latest'
    case 'project_locked': return 'Another user is editing this project: try again when they finish'
    case 'scene_managed_site': return MANAGED_SITE_COPY
    case 'proposal_expired': return d.message || 'This preview expired: generate again'
    case 'proposal_cancelled': return d.message || 'This preview was discarded: generate again'
    case 'resource_limit': return d.message || 'Too large: reduce the number of buildings'
    case 'not_found': return 'This site no longer exists: reload'
    case 'forbidden': return 'You cannot change this project: ask an editor'
    case 'integrity_error': return 'This site’s 3D scene could not be read: restore a snapshot'
    default: return 'Something went wrong: try again'
  }
}

/** Kinds a reload resolves: the caller offers Reload beside the copy. */
export const sceneErrorWantsReload = (err: unknown): boolean =>
  ['revision_conflict', 'not_found'].includes(sceneErrorDetail(err)?.error_kind ?? '')

// ── site context footer (UX SC-02 plan, condition 8) ────────────────────────

/** The footer copy whenever the site context could not be fetched; the raw reason only behind Details (UX condition 8a). */
export const CONTEXT_UNREACHABLE_COPY = 'Site context unavailable: the map data service could not be reached'

/**
 * The toast for a failed site-context fetch: the plain copy when the map data
 * service did not answer (`upstream`: a 502/504/429 or no answer), the
 * server's own words otherwise (a boundary too large), and none for a 409 (a
 * lock or a solve in flight: the read-only banner says so). Never the raw
 * upstream message (its URL, ConnectError, settings names).
 */
export function contextFailureToast(status: number | undefined, upstream: boolean, message: string): string | null {
  if (upstream) return CONTEXT_UNREACHABLE_COPY
  if (status === 409) return null
  return message || CONTEXT_UNREACHABLE_COPY
}
