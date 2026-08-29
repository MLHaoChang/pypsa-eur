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

export const CHAT_PROFILES_QUERY_KEY = ['chat', 'chat-profiles']

export function useChatProfiles(): UseQueryResult<ChatProfilesPayload> {
  return useQuery<ChatProfilesPayload>({
    queryKey: CHAT_PROFILES_QUERY_KEY,
    queryFn: getChatProfiles,
    retry: false,
  })
}
