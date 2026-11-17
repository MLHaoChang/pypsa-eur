// GC-09: the compliance workflow of a project (`/api/grid-compliance`).
// One thunk per route; the shapes mirror `services/grid_compliance/tracker.py`.
import client from './client'

export type GcStatus =
  | 'not_started' | 'data' | 'exported' | 'results_in'
  | 'judged' | 'submitted' | 'tso_comments' | 'accepted'

export type FreezeState = 'none' | 'fresh' | 'stale' | 'refreeze_needed' | 'unreadable'

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
        const header = String(r.headers?.['content-disposition'] ?? '')
        const name = /filename="([^"]+)"/.exec(header)?.[1] ?? `TRGC-${code}-pack.zip`
        return { blob: r.data, filename: name }
      }),
}
