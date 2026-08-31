/**
 * LLM provider settings — the super-admin surface, plus the member-level
 * profile list that feeds the chat panel's dropdown.
 *
 * Every shape here was read off `routers/chat.py` directly (`ProfileIn`,
 * `_profile_out`, the `/settings/llm/*` handlers) rather than from the plan's
 * sketch, because the plan predates the routes.
 *
 * TWO BOUNDARIES WORTH KNOWING BEFORE YOU EDIT THIS FILE:
 *
 * 1. `key_env` is NOT part of `LLMProfileIn`, and must never be added. The
 *    server derives it from `id`/`preset` and `ProfileIn` sets
 *    `extra="forbid"`, so sending one is a 422 — deliberately. A
 *    client-settable key slot would let a profile point `base_url` at an
 *    attacker's host while naming a well-known slot (say `ANTHROPIC_API_KEY`)
 *    as its credential, i.e. an exfiltration primitive for whatever secret
 *    already lives there.
 * 2. A key VALUE never comes back. `LLMProfileOut` carries only
 *    `key_required` / `key_present` / `key_hint` (last four characters), the
 *    same status accessor `/settings/api-key` uses.
 */
import axios from 'axios'
import { client } from './client'

/** One configured profile, as the settings surface sees it. */
export interface LLMProfileOut {
  id: string
  label: string
  preset: string
  wire: 'anthropic' | 'openai'
  /** `null` means "use the preset's declared endpoint". */
  base_url: string | null
  model: string
  tools: boolean
  vision: boolean
  auth: 'bearer' | 'none'
  fallback_model: string | null
  max_output_tokens: number | null
  /** `auth === 'bearer'`. A keyless local endpoint has no key concept. */
  key_required: boolean
  key_present: boolean
  /** Last four characters, e.g. `…wxyz`. NEVER the key. */
  key_hint: string | null
}

/** Body for create/update. Deliberately has no `key_env` and no key. */
export interface LLMProfileIn {
  label: string
  preset: string
  wire: 'anthropic' | 'openai'
  base_url?: string | null
  model: string
  tools: boolean
  vision: boolean
  auth: 'bearer' | 'none'
  fallback_model?: string | null
  max_output_tokens?: number | null
}

/** A catalogue entry — data shipped with the app, not user-authored. */
export interface PresetOut {
  id: string
  label: string
  wire: 'anthropic' | 'openai'
  base_url: string | null
  auth: 'bearer' | 'none'
  key_env: string | null
  tools: boolean
  vision: boolean
  suggested_models: string[]
  help: string
}

export interface LLMSettingsPayload {
  profiles: LLMProfileOut[]
  active_profile_id: string
  presets: PresetOut[]
}

/** Key status only — the shape `app_secrets.status` returns, minus the value. */
export interface KeyStatus {
  key_required: boolean
  key_present: boolean
  key_hint: string | null
}

/**
 * Connection-test verdict. Fixed strings by design: the server never echoes
 * a full base_url or upstream exception text into these, so the UI copy is
 * driven by the verdict, not by a message it received.
 */
export type TestVerdict =
  | 'ok'
  | 'unreachable'
  | 'unauthorized'
  | 'model_not_found'
  | 'invalid_request'

export interface TestResult {
  verdict: TestVerdict
  latency_ms: number | null
  /** Best-effort model list; `null` when the endpoint does not expose one. */
  models: string[] | null
}

/** The member-level payload that feeds the chat dropdown. */
export interface ChatProfilesPayload {
  profiles: Array<{ id: string; label: string; wire: 'anthropic' | 'openai' }>
  active_profile_id: string
}

export async function getLLMSettings(): Promise<LLMSettingsPayload> {
  const r = await client.get('/chat/settings/llm')
  return r.data
}

/**
 * `GET /chat/settings/llm`, mapped for the reachability gate the Assistant
 * Model Settings section hosts on — mirrors `fetchLocalSettings` in
 * api/localSettings.ts exactly, including `skipErrorToast`.
 *
 * A 403 (an ordinary, non-super-admin member) or a 404 (the route not
 * mounted at all, same shape a web deployment could someday produce) both
 * mean "not for you", not a failure. This wrapper — not `getLLMSettings`
 * above, whose single-argument call is pinned by llmSettings.test.ts — is
 * what the Settings nav row's availability hook and the section component
 * both call, and that hook runs on EVERY session (it decides whether to
 * show the nav row at all), so an un-suppressed 403 toast would fire for
 * every non-admin member on every load.
 */
export async function fetchLLMSettingsOrNull(): Promise<LLMSettingsPayload | null> {
  try {
    const r = await client.get<LLMSettingsPayload>('/chat/settings/llm', { skipErrorToast: true })
    return r.data
  } catch (error) {
    if (axios.isAxiosError(error) && (error.response?.status === 403 || error.response?.status === 404)) {
      return null
    }
    throw error
  }
}

export async function putLLMProfile(
  id: string,
  body: LLMProfileIn,
): Promise<LLMProfileOut> {
  const r = await client.put(`/chat/settings/llm/profiles/${id}`, body)
  return r.data
}

export async function deleteLLMProfile(
  id: string,
): Promise<{ ok: boolean; active_profile_id: string }> {
  const r = await client.delete(`/chat/settings/llm/profiles/${id}`)
  return r.data
}

export async function putLLMProfileKey(
  id: string,
  value: string,
): Promise<KeyStatus> {
  const r = await client.put(`/chat/settings/llm/profiles/${id}/key`, { value })
  return r.data
}

export async function deleteLLMProfileKey(id: string): Promise<KeyStatus> {
  const r = await client.delete(`/chat/settings/llm/profiles/${id}/key`)
  return r.data
}

export async function postLLMActive(
  profileId: string,
): Promise<{ active_profile_id: string }> {
  const r = await client.post('/chat/settings/llm/active', {
    profile_id: profileId,
  })
  return r.data
}

export async function postLLMTest(id: string): Promise<TestResult> {
  const r = await client.post(`/chat/settings/llm/profiles/${id}/test`)
  return r.data
}

/**
 * Member-level: every authenticated user may READ which profiles exist, so
 * the dropdown can render. Writing any of them is super-admin only.
 *
 * NO `skipErrorToast` here, unlike `getApiKeySettings`. That one suppresses
 * because a 403 is an expected STATE for an ordinary member. This route is
 * member-level, so a failure here is a real failure — and per ADR-0001 the
 * caller must render it as "could not load models", never as an empty list
 * that reads identically to "no models configured".
 */
export async function getChatProfiles(): Promise<ChatProfilesPayload> {
  const r = await client.get('/chat/profiles')
  return r.data
}
