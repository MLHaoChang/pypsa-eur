import { client } from './client'
export type ActivityFilter = 'all' | 'running' | 'needs_you' | 'scheduled' | 'completed'
export interface SourceGapReference { source_id: string; revision: number; digest: string }
export interface PreparedGapFollowup {
  message: string; checked_scope: Record<string, unknown>; source_gap_ref: SourceGapReference
}
export interface ActivityItem {
  id: string; source_id: string; project_id: string; family: string; title: string
  native_status: string; status: string; evidence_available: boolean; needs_you: boolean
  session_id?: string; stage?: string | null; reason?: string | null
  observation?: 'live' | 'retained'; observed_at?: number | null
  last_observed_status?: string | null
  tool_use_id?: string; decision_id?: string; decision_revision?: number; decision_digest?: string
  live_approval_available?: boolean
}
export interface DecisionObservation {
  source_id: string; project_id: string; session_id: string; tool_use_id: string
  tool_name: string; native_status: string; revision: number; decision_digest: string
  reason: string; inspection_only: true; evidence_available: false
  prepared_followup?: PreparedGapFollowup | null
  followup_unavailable_reason?: string | null
  followups?: { answer_id: string; turn_id: string; session_id: string; request_complete: boolean; observed_at: number }[]
  source_gap?: { answer_id: string; turn_id: string
    request_scope: { profile: string; project_id: string; entity_id?: string | null
      metrics?: string[]; start?: string; end?: string; window?: string
      retain?: boolean; evidence_id?: string; comparison_evidence_id?: string
      cost_basis?: { currency: string; price_year: number; annual_fixed_cost_fraction: number } }
    slots: { metric: string; disposition: 'unavailable' | 'needs_clarification'; reason: string }[] }
  question: null | { title: string; question: string; detail: string | null
    options: { label: string; description: string; recommended: boolean }[]
    allow_free_text: boolean; multi_select: boolean; intent: 'choice' | 'plan_review' }
}
export async function getAssistantDecision(item: ActivityItem, signal: AbortSignal): Promise<DecisionObservation> {
  const response = await client.get<DecisionObservation>(`/chat/decisions/${encodeURIComponent(item.decision_id!)}`, {
    params: { project_id: item.project_id, revision: item.decision_revision, digest: item.decision_digest },
    signal, skipErrorToast: true,
  })
  return response.data
}
export interface ActivitySnapshot {
  version: 1; project_id: string; filter: ActivityFilter; cursor: string; revision: number
  resnapshot_required: boolean; items: ActivityItem[]; next_page: string | null
  total: number; warnings: string[]; scheduling_available: boolean
}
export async function getAssistantActivity(
  project: string, filter: ActivityFilter,
  options: { cursor?: string; page_token?: string; signal?: AbortSignal } = {},
): Promise<ActivitySnapshot> {
  const { signal, ...params } = options
  const response = await client.get<ActivitySnapshot>('/chat/activity', {
    params: { project_id: project, filter, limit: 20, ...params }, signal, skipErrorToast: true,
  })
  return response.data
}
