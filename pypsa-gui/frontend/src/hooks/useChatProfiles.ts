/**
 * Task 13 — the member-level profile list that feeds the chat panel's model
 * dropdown.
 *
 * `retry: false`, matching `ApiKeySetup`'s `getApiKeySettings` query: a
 * failure here (network down, backend unreachable) is a real thing to show
 * the user, not a state worth silently retrying three times before the
 * dropdown admits it couldn't load — see ADR-0001 (unresolvable data ships
 * as a distinct state, never silently reinterpreted as "empty").
 *
 * Exported query key so ChatPanel's `session_init` handler and any future
 * settings-mutation success handler can invalidate the SAME cache entry this
 * hook reads, per the `API_KEY_SETTINGS_KEY` precedent in ApiKeySetup.tsx.
 */
import { useQuery, type UseQueryResult } from '@tanstack/react-query'
import { getChatProfiles, type ChatProfilesPayload } from '../api/llmSettings'
import { getChatHealth, type ChatHealth } from '../api/chat'
import { useChatStore } from '../store/chatStore'

export const CHAT_PROFILES_QUERY_KEY = ['chat', 'chat-profiles']

export function useChatProfiles(): UseQueryResult<ChatProfilesPayload> {
  return useQuery<ChatProfilesPayload>({
    queryKey: CHAT_PROFILES_QUERY_KEY,
    queryFn: getChatProfiles,
    retry: false,
  })
}

/**
 * P28 A3 (deferred spec 2026-09-28 §3.1, D-3) — whether `profileId` could
 * answer right now. The profile's own `chat_ready` from GET /chat/profiles
 * when the list says; otherwise `/health`'s `chat_ready` when it IS the
 * active profile (the list is loading, was refused — 401 for a caller with no
 * user — or comes from an older backend); otherwise `undefined` (unknown).
 * Callers gate only on an explicit `false`: a probe outage must not lock the
 * assistant.
 */
export function chatProfileReadiness(
  profileId: string | null,
  profiles: ChatProfilesPayload | undefined,
  health: ChatHealth | undefined,
): boolean | undefined {
  if (profileId == null) return undefined
  const listed = profiles?.profiles.find((p) => p.id === profileId)?.chat_ready
  if (typeof listed === 'boolean') return listed
  if (health?.active_profile?.id === profileId) return health.chat_ready
  return undefined
}

/**
 * The profile the next turn runs on and its readiness — one rule for the
 * Send gate (ChatPanel), the dropdown's display and the greeting's key offer.
 * `profileId` (the user's pick, sent with the request) wins; else the
 * session's bound profile (`/history.bound_profile_id`: `/stream` keeps a
 * bound session's binding when the request names none); else the active one.
 * The health probe shares `['chat','health']` with ApiKeySetup and
 * AssistantModelSettings, so a key save or a profile switch re-reads it.
 */
export function useChatReadiness(): { effectiveProfileId: string | null; ready: boolean | undefined } {
  const picked = useChatStore((s) => s.profileId)
  const bound = useChatStore((s) => s.boundProfileId)
  const { data: profiles } = useChatProfiles()
  const { data: health } = useQuery<ChatHealth>({
    queryKey: ['chat', 'health'],
    queryFn: getChatHealth,
    staleTime: 30_000,
    retry: false,
  })
  const effectiveProfileId = picked ?? bound
    ?? profiles?.active_profile_id ?? health?.active_profile?.id ?? null
  return { effectiveProfileId, ready: chatProfileReadiness(effectiveProfileId, profiles, health) }
}
