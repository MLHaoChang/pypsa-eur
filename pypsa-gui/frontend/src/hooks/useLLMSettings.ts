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
 * below matches it for the 403 case. C-11 later corrected the OUTAGE case:
 * nav-row gating now also admits `isError`, because gating it out made the
 * settings SECTION's own outage state unreachable on the one deployment
 * where this is the only gate.
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

/**
 * True once we know the route exists and answers for this user, OR when it
 * failed in a way that is worth telling them about.
 *
 * C-11 — this was `data != null`, and `data` is null on BOTH "not for you"
 * (403/404, which `fetchLLMSettingsOrNull` maps to a RESOLVED null) and a
 * genuine outage (5xx/network, which surfaces as `isError`). Only the first
 * should hide the door. On a web deployment — where local-settings 404s, so
 * this is the only gate — an llm-settings 500 hid the Settings row entirely,
 * and with `staleTime: Infinity` and no refetch-on-focus it stayed hidden for
 * the rest of the session. The two-layer outage state built inside
 * AssistantModelSettings could therefore never be reached by the person it
 * was written for.
 *
 * A 403 still hides the row, so widening this does not start showing an empty
 * pane to every ordinary member — asserted by its own sibling test.
 */
export function useLLMSettingsAvailable(): boolean {
  const { data, isError } = useLLMSettings()
  return data != null || isError
}

export function useInvalidateLLMSettings() {
  const qc = useQueryClient()
  return () => qc.invalidateQueries({ queryKey: LLM_SETTINGS_KEY })
}
