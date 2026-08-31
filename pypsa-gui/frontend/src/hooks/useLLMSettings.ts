/**
 * One fetch of /chat/settings/llm, shared by the Assistant Model Settings
 * section and the nav row that gates on its reachability. Mirrors
 * hooks/useLocalSettings.ts's SHAPE — same query options, same reasoning for
 * `staleTime`/`retry` — because this is the same kind of gate: a settings
 * surface that is not available to every deployment/every user.
 *
 * `data === null` means the route answered "not for you" (403 for an
 * ordinary member, 404 if the route is ever unmounted) — see
 * `fetchLLMSettingsOrNull` in api/llmSettings.ts.
 *
 * DELIBERATE DIVERGENCE FROM `useLocalSettings` (fix round 1, ADR-0001 in a
 * new place): this hook's `data === null` state and its `isError` state mean
 * DIFFERENT things, and `AssistantModelSettings` reads both — `data === null`
 * renders nothing ("not for you"), `isError` renders a visible outage state
 * with a retry affordance. Collapsing "a real 500/network failure" into the
 * same silent-nothing as "an ordinary member" would hide a genuine outage
 * from the one audience (super-admins) who could act on it.
 * `useLocalSettingsAvailable` (hooks/useLocalSettings.ts) still folds
 * `isError` into "unavailable" — a pre-existing, deliberate choice this task
 * does not widen; see that hook's own header. `useLLMSettingsAvailable`
 * below matches it (nav-row gating is unchanged by this fix — only the
 * settings SECTION's own render gained the distinct outage state).
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
