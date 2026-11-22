// GC-09: the compliance workflow of a project (`/api/grid-compliance`).
// One thunk per route; the shapes mirror `services/grid_compliance/tracker.py`.
import client from './client'
import { errorText } from './campusElectrical'
import { fileNameOf } from './resultsEvidence'
import { withJsonDetail } from './siteScene'

export type GcStatus =
  | 'not_started' | 'data' | 'exported' | 'results_in'
  | 'judged' | 'submitted' | 'tso_comments' | 'accepted'

export type FreezeState = 'none' | 'fresh' | 'stale' | 'refreeze_needed' | 'unreadable' | 'submitted'

export interface Freeze { model_set_id: string; data_hash: string; frozen_at: string; source: string }

export interface DataItem {
  item_id: string
  label: string
  supplier: string
  due: string | null
  status: 'requested' | 'received' | 'checked'
}

export interface TsoComment {
  comment_id: string
  date: string
  clause: string
  comment: string
  response: string
  revision: string
  status: 'open' | 'responded' | 'closed'
  pack: { version: number; digest: string } | null
  created_at: string
  updated_at: string
}

export interface Deliverable {
  code: string
  title: string
  phase: number
  tool: string
  depends_on: string[]
  blocked_by: string[]
  status: GcStatus
  status_basis: 'declared'
  status_at: string | null
  next_step: string
  data: DataItem[]
  freeze: Freeze | null
  freeze_state: FreezeState
  stale: boolean
  /** The model set each submission went out on, oldest first; never rewritten. */
  submissions: Freeze[]
  /** Submitted rows only: the current inputs against the kept record. */
  since_submission: { inputs: 'unchanged' | 'changed' | 'not_compared'; reason: string | null } | null
  feeds: { package: string; label: string; available: boolean; state: 'present' | 'pending' | 'unavailable' }[]
  comments: TsoComment[]
  packs: { version: number; digest: string }[]
}

export interface ComplianceState {
  statuses: GcStatus[]
  phases: Record<string, string>
  has_campus_input: boolean
  deliverables: Deliverable[]
}

export interface NewComment {
  date: string
  clause: string
  comment: string
}

export interface CommentPatch {
  response?: string
  revision?: string
  status?: TsoComment['status']
  pack_version?: number
}

/** A refusal body: `detail` of every 4xx/5xx from the router. */
export interface GcRefusal {
  error_kind: string
  message: string
  blockers?: string[]
  field?: string
}

/** `quiet` only drops the global toast: the interceptor still rejects, and
 *  each caller shows the refusal inline with `gcErrorText`. */
const quiet = { skipErrorToast: true } as const
const base = (project: string) => `/grid-compliance/${encodeURIComponent(project)}`
const one = (project: string, code: string) =>
  `${base(project)}/deliverables/${encodeURIComponent(code)}`

export const GRID_COMPLIANCE_KEY = (project: string) => ['grid-compliance', project] as const

export const gridComplianceApi = {
  state: (project: string) =>
    client.get<ComplianceState>(base(project), quiet).then(r => r.data),
  setStatus: (project: string, code: string, status: GcStatus) =>
    client.put<Deliverable>(`${one(project, code)}/status`, { status }, quiet).then(r => r.data),
  setData: (project: string, code: string, items: DataItem[]) =>
    client.put<Deliverable>(`${one(project, code)}/data`, { items }, quiet).then(r => r.data),
  freeze: (project: string, code: string) =>
    client.post<Deliverable>(`${one(project, code)}/freeze`, undefined, quiet).then(r => r.data),
  addComment: (project: string, code: string, body: NewComment) =>
    client.post<TsoComment>(`${one(project, code)}/comments`, body, quiet).then(r => r.data),
  updateComment: (project: string, code: string, id: string, body: CommentPatch) =>
    client.patch<TsoComment>(`${one(project, code)}/comments/${encodeURIComponent(id)}`, body, quiet)
      .then(r => r.data),
  /** The pack ZIP and its file name (from Content-Disposition). */
  pack: (project: string, code: string) =>
    client.post<Blob>(`${one(project, code)}/pack`, undefined, { ...quiet, responseType: 'blob' })
      .then(r => {
        const filename = fileNameOf(r.headers?.['content-disposition']) ?? `TRGC-${code}-pack.zip`
        return { blob: r.data, filename }
      })
      // a blob request's refusal carries its JSON detail as a Blob
      .catch(async (err: unknown) => { throw await withJsonDetail(err) }),
}

/** The words of an input field the server names in a `gc_invalid` refusal. */
const FIELD_LABEL: Record<string, string> = {
  item_id: 'Item id', label: 'Item', supplier: 'Supplier', due: 'Due date', status: 'Status',
  date: 'Comment date', clause: 'Clause', comment: 'TSO comment', response: 'Response',
  revision: 'Revision', pack_version: 'Pack',
}

/** A refusal in the server's words; a `gc_invalid` names its field in words. */
export function gcErrorText(e: unknown): string {
  const text = errorText(e)
  const detail = (e as { response?: { data?: { detail?: Partial<GcRefusal> } } } | null)?.response?.data?.detail
  const field = detail?.error_kind === 'gc_invalid' && typeof detail.field === 'string' ? detail.field : null
  if (!field) return text
  const label = FIELD_LABEL[field.split('.').at(-1) ?? ''] ?? 'A field'
  return text.startsWith(`${field}: `) ? `${label}: ${text.slice(field.length + 2)}` : `${label}: ${text}`
}
