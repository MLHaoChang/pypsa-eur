/**
 * One fetch of /chat/settings/llm, shared by the Assistant Model Settings
 * section and the nav row that gates on its reachability. Mirrors
 * hooks/useLocalSettings.ts exactly — same shape, same reasoning — because
 * this is the SAME kind of gate: a settings surface that is not available to
 * every deployment/every user, and must hide rather than error when it
 * isn't.
 *
 * `data === null` means the route answered "not for you" (403 for an
 * ordinary member, 404 if the route is ever unmounted) — see
 * `fetchLLMSettingsOrNull` in api/llmSettings.ts. BOTH consumers hide
 * themselves on it. A nav entry that opens an empty section is worse than no
 * nav entry.
 *
 * `staleTime: Infinity`: nothing here changes except through mutations this
 * same section performs, which invalidate the key explicitly after a write.
 *
 * `retry: 2`, deliberately not `false` — same reasoning as
 * useLocalSettings.ts: this query fetches once per session and then never
 * again on its own, so a single transient GET failure must not hide the
 * Sidebar row AND the ⌘K entry for the rest of the session.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchLLMSettingsOrNull, type LLMSettingsPayload } from '../api/llmSettings'

export const LLM_SETTINGS_KEY = ['llmSettings'] as const

export function useLLMSettings() {
  return useQuery<LLMSettingsPayload | null>({
    queryKey: LLM_SETTINGS_KEY,
    queryFn: fetchLLMSettingsOrNull,
    staleTime: Infinity,
    retry: 2,
  })
}

/** True only once we know the route exists AND answers for this user. */
export function useLLMSettingsAvailable(): boolean {
  const { data } = useLLMSettings()
  return data != null
}

export function useInvalidateLLMSettings() {
  const qc = useQueryClient()
  return () => qc.invalidateQueries({ queryKey: LLM_SETTINGS_KEY })
}
