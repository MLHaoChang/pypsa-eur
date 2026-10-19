import { client } from './client'

export type ChatRestrictionMode = 'standard' | 'confirm_changes' | 'read_only'
export interface ChatRestriction { mode: ChatRestrictionMode; revision: string }

export interface ChatAccessSnapshot {
  version: 1
  read_only: true
  observed_at: number
  scope: { project_id: string | null; project_name: string | null }
  selection: { ui_mode: 'expert' | 'guided'; checked_requested: boolean }
  restrictions: ChatRestriction | null
  confirmation_rules: Array<{ tier: string; native: 'none' | 'policy' | 'user' | 'blocked'; external: 'none' | 'policy' | 'user' | 'blocked' }>
  project_lock: 'unlocked' | 'yours' | 'other' | 'not_applicable'
  blockers: string[]
  session: { id: string; output_tokens_used: number | null; turn_in_flight: boolean } | null
  checking: { server_mode: string; effective_for_selection: boolean; scope: 'bounded_profiles' }
  limits: {
    output_tokens_per_turn: number; tool_calls_per_turn: number; output_tokens_per_session: number
    turns_without_reported_usage: number; daily_project_tokens: number | null; confirmation_ttl_seconds: number
  }
}

export async function getChatAccess(project: string | null, session: string | null,
  mode: 'expert' | 'guided', checked: boolean, signal?: AbortSignal): Promise<ChatAccessSnapshot> {
  const { data } = await client.get<ChatAccessSnapshot>('/chat/access', {
    params: { project_id: project ?? undefined, session_id: session ?? undefined, ui_mode: mode, checked }, signal,
  })
  return data
}

export async function saveChatRestriction(project: string, revision: string, mode: ChatRestrictionMode):
Promise<ChatRestriction & { project_id: string }> {
  const { data } = await client.put<ChatRestriction & { project_id: string }>('/chat/restrictions', {
    project_id: project, revision, mode,
  })
  return data
}
