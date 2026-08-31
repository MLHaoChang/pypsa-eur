/**
 * Desktop-only settings: the Anthropic API key and the application log.
 *
 * Every route here 404s on a web deployment (the backend gates them with
 * `reject_unless_local_mode`). `fetchLocalSettings` maps that 404 to `null`
 * rather than an error, which is how the pane and its nav entry know to hide
 * themselves — the same shape `listUnclaimed` uses at projects.ts:111.
 *
 * NOTE (Task 15, LLM provider config): the key this module writes IS
 * `ANTHROPIC_API_KEY` — the same secret slot `api/chat.ts`'s
 * `/chat/settings/api-key` writes (ApiKeySetup.tsx) and the same one the two
 * built-in Claude profiles' `key_env` resolves to (`services/llm_config.py`,
 * backend). It is not "the chat key" any more, now that a deployment can
 * have several LLM profiles on other providers — `keyFieldPlaceholder`'s
 * `'sk-ant-…'` below stays correct precisely because it is scoped to that one
 * slot, not to "the" assistant key; the pane's own copy (pages/LocalSettings.
 * tsx) says so explicitly rather than implying this is the only key that
 * matters.
 */
import axios from 'axios'
import { client } from './client'

export type ProbeStatus =
  | 'valid'
  | 'rejected'
  | 'unreachable'
  | 'sdk_not_installed'
  | 'cleared'

export interface LocalSettingsState {
  key_set: boolean
  /** Last four characters, or null — including when the key is too short to hint safely. */
  key_hint: string | null
  log_path: string
}

export interface PutKeyResponse extends LocalSettingsState {
  status: ProbeStatus
  detail: string
}

export interface RevealResponse {
  revealed: boolean
  detail?: string
  log_path: string
}

/** `null` means "this build is not the desktop app" — not an error. */
export async function fetchLocalSettings(): Promise<LocalSettingsState | null> {
  try {
    const { data } = await client.get<LocalSettingsState>('/local-settings', {
      skipErrorToast: true,
    })
    return data
  } catch (error) {
    if (axios.isAxiosError(error) && error.response?.status === 404) return null
    throw error
  }
}

export async function putApiKey(apiKey: string): Promise<PutKeyResponse> {
  const { data } = await client.put<PutKeyResponse>(
    '/local-settings/anthropic-key',
    { api_key: apiKey },
  )
  return data
}

export async function revealLog(): Promise<RevealResponse> {
  const { data } = await client.post<RevealResponse>(
    '/local-settings/reveal-log',
    {},
    { skipErrorToast: true },
  )
  return data
}

export function keyFieldPlaceholder(state: LocalSettingsState | null): string {
  if (!state?.key_set) return 'sk-ant-…'
  return state.key_hint ? `Key set — ending ${state.key_hint}` : 'Key set'
}

export interface ProbeMessage {
  tone: 'ok' | 'warn' | 'error'
  text: string
}

/**
 * One message per status, and never a shared one.
 *
 * `unreachable` is a WARNING, not a success and not an error: the key is
 * stored, and whether it works is genuinely unknown. Collapsing it into either
 * neighbour is the same defect as reporting an unresolvable cost as zero.
 */
export function probeMessage(status: ProbeStatus): ProbeMessage {
  switch (status) {
    case 'valid':
      return { tone: 'ok', text: 'Key accepted — chat is enabled.' }
    case 'rejected':
      return {
        tone: 'error',
        text: 'Anthropic rejected this key. It was saved anyway; chat stays disabled.',
      }
    case 'unreachable':
      return {
        tone: 'warn',
        text: 'Saved, but Anthropic could not be reached — the key is unverified.',
      }
    case 'sdk_not_installed':
      return { tone: 'error', text: 'The anthropic package is missing from this build.' }
    case 'cleared':
      return { tone: 'ok', text: 'Key removed. Chat is now disabled.' }
  }
}
