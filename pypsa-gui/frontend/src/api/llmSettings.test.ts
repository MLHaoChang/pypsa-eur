import { beforeEach, describe, expect, it, vi } from 'vitest'

// `client` is the shared axios instance (interceptors, CSRF, error toasts).
// Mocking it keeps these tests about THIS module's contract with the backend
// — the URL each function calls, the body it sends, what it returns — rather
// than re-testing axios.
const get = vi.fn()
const put = vi.fn()
const post = vi.fn()
const del = vi.fn()
vi.mock('./client', () => ({
  client: {
    get: (...a: unknown[]) => get(...a),
    put: (...a: unknown[]) => put(...a),
    post: (...a: unknown[]) => post(...a),
    delete: (...a: unknown[]) => del(...a),
  },
}))

import {
  deleteLLMProfile,
  deleteLLMProfileKey,
  getChatProfiles,
  getLLMSettings,
  postLLMActive,
  postLLMTest,
  putLLMProfile,
  putLLMProfileKey,
  type LLMProfileIn,
} from './llmSettings'

beforeEach(() => {
  get.mockReset(); put.mockReset(); post.mockReset(); del.mockReset()
})

describe('llmSettings client', () => {
  it('reads the settings payload from the super-admin route', async () => {
    get.mockResolvedValue({ data: { profiles: [], active_profile_id: 'x', presets: [] } })
    const out = await getLLMSettings()
    expect(get).toHaveBeenCalledWith('/chat/settings/llm')
    expect(out.active_profile_id).toBe('x')
  })

  it('reads the dropdown list from the MEMBER-level route', async () => {
    // Distinct from /chat/settings/llm: every authenticated user may read
    // which profiles exist so the picker can render; only a super-admin
    // writes them.
    get.mockResolvedValue({
      data: { profiles: [{ id: 'a', label: 'A', wire: 'anthropic' }], active_profile_id: 'a' },
    })
    const out = await getChatProfiles()
    expect(get).toHaveBeenCalledWith('/chat/profiles')
    expect(out.profiles[0].label).toBe('A')
  })

  it('does NOT suppress errors on the member route', async () => {
    // getApiKeySettings passes { skipErrorToast: true } because a 403 there is
    // an expected STATE for a member. This route IS member-level, so a failure
    // is a real failure and must surface — per ADR-0001 the caller has to be
    // able to tell "could not load" from "nothing configured".
    get.mockResolvedValue({ data: { profiles: [], active_profile_id: 'a' } })
    await getChatProfiles()
    expect(get).toHaveBeenCalledWith('/chat/profiles')
    expect(get.mock.calls[0]).toHaveLength(1) // no options object at all
  })

  it('sends a profile body with no key_env and no key', async () => {
    // The server sets extra="forbid", so a stray field is a 422. More to the
    // point: a client-settable key slot would let a profile aim base_url at
    // an attacker host while naming a well-known secret's slot.
    const body: LLMProfileIn = {
      label: 'Ollama', preset: 'custom', wire: 'openai',
      base_url: 'http://localhost:11434/v1', model: 'qwen3:8b',
      tools: true, vision: false, auth: 'none',
    }
    put.mockResolvedValue({ data: { ...body, id: 'ollama' } })
    await putLLMProfile('ollama', body)
    expect(put).toHaveBeenCalledWith('/chat/settings/llm/profiles/ollama', body)
    const sent = put.mock.calls[0][1] as Record<string, unknown>
    expect(sent).not.toHaveProperty('key_env')
    expect(sent).not.toHaveProperty('key')
    expect(sent).not.toHaveProperty('value')
  })

  it('sets and clears a per-profile key on its own sub-route', async () => {
    put.mockResolvedValue({ data: { key_required: true, key_present: true, key_hint: '…wxyz' } })
    const set = await putLLMProfileKey('openai', 'sk-secret')
    expect(put).toHaveBeenCalledWith('/chat/settings/llm/profiles/openai/key', { value: 'sk-secret' })
    expect(set.key_hint).toBe('…wxyz')

    del.mockResolvedValue({ data: { key_required: true, key_present: false, key_hint: null } })
    const cleared = await deleteLLMProfileKey('openai')
    expect(del).toHaveBeenCalledWith('/chat/settings/llm/profiles/openai/key')
    expect(cleared.key_present).toBe(false)
  })

  it('deletes a profile and reports the resulting active id', async () => {
    del.mockResolvedValue({ data: { ok: true, active_profile_id: 'anthropic-sonnet' } })
    const out = await deleteLLMProfile('gone')
    expect(del).toHaveBeenCalledWith('/chat/settings/llm/profiles/gone')
    expect(out.active_profile_id).toBe('anthropic-sonnet')
  })

  it('switches the active profile', async () => {
    post.mockResolvedValue({ data: { active_profile_id: 'anthropic-opus' } })
    const out = await postLLMActive('anthropic-opus')
    expect(post).toHaveBeenCalledWith('/chat/settings/llm/active', { profile_id: 'anthropic-opus' })
    expect(out.active_profile_id).toBe('anthropic-opus')
  })

  it('returns a typed connection-test verdict', async () => {
    post.mockResolvedValue({ data: { verdict: 'unreachable', latency_ms: null, models: null } })
    const out = await postLLMTest('ollama')
    expect(post).toHaveBeenCalledWith('/chat/settings/llm/profiles/ollama/test')
    expect(out.verdict).toBe('unreachable')
    // Fixed verdict strings are the contract: the server never echoes a full
    // base_url or upstream exception text, so UI copy keys off the verdict.
    expect(out.models).toBeNull()
  })
})
