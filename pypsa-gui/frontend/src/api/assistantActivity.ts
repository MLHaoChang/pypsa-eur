import { client } from './client'
export type ActivityFilter = 'all' | 'running' | 'needs_you' | 'scheduled' | 'completed'
export interface ActivityItem {
  id: string; source_id: string; project_id: string; family: string; title: string
  native_status: string; status: string; evidence_available: boolean; needs_you: boolean
  session_id?: string; stage?: string | null; reason?: string | null
  observation?: 'live' | 'retained'; observed_at?: number | null
  last_observed_status?: string | null
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
