import client, { formatApiDetail } from './client'

// Assistant memory (`/api/memory/*`, plan §4; routes in backend/routers/memory.py).
// Not a project route: memory spans projects. Every miss is a 404, so an id
// that is not the caller's reads the same as one that does not exist.

export type MemoryKind = 'preference' | 'chart_spec' | 'parameter' | 'lesson'
export type MemoryStatus = 'proposed' | 'confirmed' | 'retired' | 'dismissed'
export type ProjectKind = 'capacity_expansion' | 'planning_dynamics' | 'connection'

/** One evidence link as `memory_store.evidence_for` renders it for the viewer:
 * a project the viewer cannot open already comes back without its name. */
export interface MemoryEvidence {
  kind: 'project' | 'run' | 'chat' | 'answer'
  id?: string | null
  label: string
  at?: string | null
  /** false: the viewer cannot open the source project (no name shown). */
  access?: boolean
}

export interface MemoryItem {
  id: string
  kind: MemoryKind
  scope: 'user' | 'org'
  applies_to: ProjectKind | null
  concept_keys: string[]
  status: MemoryStatus
  origin: string
  version: number
  summary: string
  body: Record<string, unknown> | null
  evidence: MemoryEvidence[]
  nomination: 'pending' | 'declined' | null
  promoted_from: string | null
  recall_count: number
  apply_count: number
  last_used_at: string | null
  created_at: string
  updated_at: string
}

export interface MemoryVersion {
  version: number
  summary: string
  body: Record<string, unknown> | null
  evidence: MemoryEvidence[]
  edited_by?: string | null
  edited_at?: string | null
}

export interface ListParams {
  kind?: MemoryKind
  status?: MemoryStatus | 'all'
  scope?: 'user' | 'org'
  nominated?: boolean
}

export interface NewMemoryItem {
  kind: 'preference' | 'chart_spec' | 'lesson'
  body: Record<string, unknown>
  concept_keys: string[]
  project_kind?: ProjectKind | null
  summary?: string
}

export interface MemoryEdit {
  body?: Record<string, unknown>
  summary?: string
}

export type MemoryAction =
  | 'confirm' | 'dismiss' | 'retire' | 'restore' | 'nominate' | 'promote' | 'decline'

export const memoryApi = {
  list: (params: ListParams = {}) =>
    client.get<{ items: MemoryItem[]; can_promote: boolean }>('/memory/items', { params })
      .then(r => r.data),
  create: (item: NewMemoryItem) =>
    client.post<MemoryItem>('/memory/items', item).then(r => r.data),
  edit: (id: string, edit: MemoryEdit) =>
    client.patch<MemoryItem>(`/memory/items/${encodeURIComponent(id)}`, edit).then(r => r.data),
  act: (id: string, action: MemoryAction) =>
    client.post<MemoryItem>(`/memory/items/${encodeURIComponent(id)}/${action}`).then(r => r.data),
  versions: (id: string) =>
    client.get<{ id: string; versions: MemoryVersion[] }>(
      `/memory/items/${encodeURIComponent(id)}/versions`,
    ).then(r => r.data),
  /** "Not now" on a memory card: the server reads the card's proposal from the
   * caller's own chat, and the same proposal is not offered for 30 days. */
  dismiss: (card: { chat_id: string; tool_use_id: string }) =>
    client.post<MemoryItem>('/memory/dismissals', card).then(r => r.data),
}

export const KIND_WORDS: Record<MemoryKind, string> = {
  preference: 'Preference',
  chart_spec: 'Chart',
  parameter: 'Parameter',
  lesson: 'Lesson',
}

export const PROJECT_KIND_WORDS: Record<ProjectKind, string> = {
  capacity_expansion: 'capacity expansion',
  planning_dynamics: 'planning dynamics',
  connection: 'connection',
}

/** "Any project" or "Capacity expansion projects". */
export function appliesToWords(kind: ProjectKind | null | undefined): string {
  if (!kind) return 'Any project'
  const w = PROJECT_KIND_WORDS[kind] ?? kind
  return `${w.charAt(0).toUpperCase()}${w.slice(1)} projects`
}

/** A concept key as plain words: `view:asset` → "Asset Detail". */
// Plain words for concept keys (UX M2). Keys match the backend registries
// (`result_views.RESULT_VIEWS` labels, `memory_store.TOPICS`); the backend
// test `test_memory_concept_words_parity` holds them in step.
export const VIEW_WORDS: Record<string, string> = {
  summary: 'Summary', overview: 'Overview', capex: 'Capacity expansion', dispatch: 'Dispatch',
  flows: 'Energy flow', loadshape: 'Load shape', loadflow: 'Load Flow',
  grid_code: 'Grid code & connection', electrical_design: 'Electrical design',
  island: 'Island & ride-through', grid_forming: 'Grid-forming', prices: 'Prices',
  economics: 'Economics', emissions: 'Emissions', curtailment: 'Curtailment',
  lostload: 'Lost load', adequacy: 'Adequacy', storage: 'Storage cycling', fmea: 'FMEA',
  grid_risk: 'Grid risk to production', investment: 'Investment case', asset: 'Asset Detail',
  profiles_pending: 'Profile studio', profile_preview: 'Profile studio',
}
export const TOPIC_WORDS: Record<string, string> = {
  units: 'Units', currency: 'Currency', style: 'Style', sensitivity: 'Sensitivity',
}
export function conceptWords(key: string): string {
  const [ns, id = ''] = key.split(':', 2)
  if (ns === 'view') return VIEW_WORDS[id] ?? id.replace(/_/g, ' ')
  if (ns === 'topic') return TOPIC_WORDS[id] ?? id.replace(/_/g, ' ')
  if (ns === 'param') return id
  return id.replace(/_/g, ' ')
}

/** The message of a memory route's typed refusal (`{error_kind, message}`). */
export function errorWords(err: unknown): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (detail && typeof detail === 'object' && 'message' in (detail as object)) {
    return String((detail as { message: unknown }).message)
  }
  return formatApiDetail(detail, (err as Error)?.message ?? 'Request failed')
}
