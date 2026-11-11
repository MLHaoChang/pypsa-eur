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

// ── Slice 04b: "Review my recent work", the dream job (routers/memory_dream.py,
// `dream.view()`). Refusals are shown in place, so these skip the toast.

export type DreamStatus = 'queued' | 'running' | 'done' | 'failed' | 'capped' | 'cancelled'

export interface DreamJob {
  id: string
  trigger: 'manual' | 'nightly' | 'sessions'
  status: DreamStatus
  error_kind: string | null
  counts: Record<string, number>
  spent: { input_tokens?: number; output_tokens?: number; calls?: number }
  budget: { tokens?: number; daily_tokens?: number }
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface DreamSettings {
  nightly: boolean
  after_sessions: boolean
  nightly_at: string
  sessions_needed: number
  /** The model a review sends past chats to (the `memory_analyzer` profile,
   * else the active one); null when it cannot be read. */
  analyzer: string | null
}

export const dreamApi = {
  latest: () =>
    client.get<{ job: DreamJob | null }>('/memory/dream', { skipErrorToast: true })
      .then(r => r.data.job),
  start: () =>
    client.post<{ job: DreamJob }>('/memory/dream', undefined, { skipErrorToast: true })
      .then(r => r.data.job),
  settings: () =>
    client.get<{ settings: DreamSettings }>('/memory/dream/settings', { skipErrorToast: true })
      .then(r => r.data.settings),
  setSettings: (s: Partial<Pick<DreamSettings, 'nightly' | 'after_sessions'>>) =>
    client.put<{ settings: DreamSettings }>('/memory/dream/settings', s, { skipErrorToast: true })
      .then(r => r.data.settings),
}

export const isDreamLive = (job: DreamJob | null | undefined) =>
  job?.status === 'queued' || job?.status === 'running'

const DREAM_FAILURE: Record<string, string> = {
  dream_provider_error: 'the model could not be reached',
  dream_restarts: 'it was interrupted too often',
  dream_user_inactive: 'your account is not active',
  dream_internal_error: 'of an internal error',
}

const plural = (n: number, one: string) => `${n} ${one}${n === 1 ? '' : 's'}`

/** One line for the latest review, in the user's words. */
export function dreamStatusWords(job: DreamJob | null | undefined): string {
  if (!job) return 'Not reviewed yet.'
  const proposed = job.counts.proposed ?? 0
  const read = job.counts.analyzed ?? 0
  const by = job.trigger === 'nightly' ? ' (nightly)' : job.trigger === 'sessions' ? ' (after new chats)' : ''
  switch (job.status) {
    case 'queued': return `Review queued${by}…`
    case 'running': return `Reviewing${by}… ${read} of your chats and runs read so far.`
    case 'done': return proposed
      ? `Last review${by}: ${plural(proposed, 'proposal')} below.`
      : `Last review${by}: nothing new to propose.`
    case 'capped': return `Last review${by} reached its token budget: ${plural(proposed, 'proposal')} kept. The next review continues from there.`
    case 'failed': return `Last review${by} failed because ${DREAM_FAILURE[job.error_kind ?? ''] ?? 'of an unexpected error'}.`
    case 'cancelled': return `Last review${by} was cancelled.`
  }
}

// ── Slice 03: saved parameter values (routes in routers/memory.py and
// routers/network_memory_parameter.py). Each refusal is shown in place, so none
// of these toasts through the client's interceptor (`skipErrorToast`).

/** A saved value offered beside a field: stored units, as saved (no conversion). */
export interface ParameterChip {
  id: string
  version: number
  scope: 'user' | 'org'
  value: number
  unit: string
  currency: string | null
  currency_year: number | null
  carrier: string | null
  /** The server's own equality with the field's value (GK B 47 A3). */
  current: boolean
  saved_at: string
  source: { label: string | null; id: string | null; access: boolean } | null
}

export interface ParameterSuggestions {
  fields: Record<string, ParameterChip[]>
  /** The fields Save for reuse accepts now: Save's own predicate (GK B 47 A1). */
  saveable: string[]
}

export interface ParameterTarget {
  project_id: string
  component_class: string
  name: string
}

/** The memory block of a preview: every fact its digest binds (GK B P4). */
export interface ParameterMemory {
  item: string
  version: number
  field: string
  label: string
  value: number
  unit: string
  unit_display: string
  currency: string | null
  currency_year: number | null
  carrier: string | null
  target: { component_class: string; name: string; value: number | null; source: string | null }
}

export interface ParameterPreview {
  memory: ParameterMemory
  memory_note: string
  /** The approval digest: the edit card's digest and the memory block. Posted untouched. */
  digest: string
  card_digest: string
  [card: string]: unknown
}

export interface ParameterApplied {
  component_class: string
  name: string
  written: Record<string, number>
  source: string
  memory: ParameterMemory
  apply_counted: boolean
}

export const memoryParameterApi = {
  suggestions: (t: ParameterTarget) =>
    client.get<ParameterSuggestions>('/memory/suggestions', { params: t, skipErrorToast: true })
      .then(r => r.data),
  /** Save for reuse: which field only; the server reads the value (GK B P1). */
  save: (t: ParameterTarget & { field: string }) =>
    client.post<MemoryItem & { created: boolean }>('/memory/parameters', t, { skipErrorToast: true })
      .then(r => r.data),
  preview: (chip: Pick<ParameterChip, 'id' | 'version'>, t: ParameterTarget) =>
    client.post<ParameterPreview>(
      `/memory/suggestions/${encodeURIComponent(chip.id)}/preview`,
      { ...t, version: chip.version }, { skipErrorToast: true },
    ).then(r => r.data),
  /** The body is built from the preview alone: the ids and digest it showed (GK B 47 A4). */
  apply: (p: ParameterPreview, projectId: string) =>
    client.post<ParameterApplied>('/network/memory_parameter/apply', {
      item_id: p.memory.item,
      version: p.memory.version,
      project_id: projectId,
      component_class: p.memory.target.component_class,
      name: p.memory.target.name,
      digest: p.digest,
    }, { skipErrorToast: true }).then(r => r.data),
}

/** The `error_kind` of a typed refusal, if any. */
export function errorKind(err: unknown): string | null {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (detail && typeof detail === 'object' && 'error_kind' in (detail as object)) {
    return String((detail as { error_kind: unknown }).error_kind)
  }
  return null
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
  // A parameter key is a class path (Generator.p_nom); show the class only (UI-2).
  if (ns === 'param') return `${id.split('.', 1)[0]} setting`
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
