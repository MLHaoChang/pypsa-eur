/**
 * U-1 — the in-app way to supply an Anthropic API key.
 *
 * WHERE THIS RENDERS, AND WHY HERE. The packaged app ships no `backend/.env`
 * (it carries a real key and the session-signing SECRET_KEY), so the chat panel
 * was permanently disabled in the shipped artifact. The user's whole experience
 * of that was a red "API key missing" banner with no next step and nothing in
 * the UI to act on. So the fix lives exactly where the problem surfaces —
 * inside that banner — rather than on a settings page the user has no reason to
 * visit and, in the desktop app, could not use anyway: `main.py` mounts the
 * admin router behind `reject_in_local_mode`, so every `/api/admin/*` route
 * 404s there.
 *
 * WHO SEES THE FORM. The route is super-admin only, because one
 * `ANTHROPIC_API_KEY` is shared by every organisation on the instance. Local
 * mode seeds its single user with `is_super_admin=True`, so the desktop app —
 * the deployment this exists for — always gets the form. A member of a
 * multi-tenant instance gets told who to ask instead, which is the only useful
 * thing to tell them.
 *
 * TASK 15 GENERALISATION. `missing_api_key` can now fire for ANY profile, not
 * only the two built-in Claude ones — a deployment can have several LLM
 * profiles on other providers (see api/llmSettings.ts). The inline form below
 * is specific to `/chat/settings/api-key`, i.e. `ANTHROPIC_API_KEY` — it makes
 * no sense, and would actively mislead, for a missing OpenAI/local-endpoint
 * key. `getChatHealth()`'s `active_profile` decides the branch: the built-in
 * profile keeps this file's original, first-run-tested form verbatim; any
 * other active profile gets told which profile is short a key and a deep-link
 * to the AssistantModelSettings section (Task 15) instead, where that
 * profile's OWN key field lives.
 */
import { useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'

import {
  deleteApiKeySettings,
  getApiKeySettings,
  getChatHealth,
  putApiKeySettings,
  type ApiKeySettings,
  type ChatHealth,
} from '../api/chat'
import { useChatStore } from '../store/chatStore'
import { useUIStore } from '../store/uiStore'

// Exported so ChatLaunchGreeting reads the SAME cache entry rather than a
// parallel one: the greeting only needs to know whether a key exists, and
// sharing the key means one fetch, and means this component's post-save
// invalidation retracts the greeting's offer in the same tick.
export const API_KEY_SETTINGS_KEY = ['chat', 'api-key-settings']

/**
 * The two ids `services/llm_config.py` synthesizes in code (`_BUILTIN_IDS`)
 * and never lets a file entry override. Kept as a local literal (rather than
 * importing AssistantModelSettings' copy) so this component's dependency
 * surface stays "chat + ui store", matching its existing shape — the ids
 * themselves are backend-stable, not something either file owns.
 */
const BUILTIN_ANTHROPIC_PROFILE_IDS = new Set(['anthropic-sonnet', 'anthropic-opus'])

/** The 403 an ordinary member gets — an expected state, not a failure. */
function isForbidden(error: unknown): boolean {
  return (error as { response?: { status?: number } })?.response?.status === 403
}

export default function ApiKeySetup() {
  const qc = useQueryClient()
  const setError = useChatStore((s) => s.setError)
  const requestSettingsSection = useUIStore((s) => s.requestSettingsSection)
  const setSlidePanel = useUIStore((s) => s.setSlidePanel)
  const [value, setValue] = useState('')

  // Cheap, unauthenticated-adjacent probe (api/chat.ts's own doc comment) —
  // this is the first real caller. Decides which branch below renders;
  // see the TASK 15 GENERALISATION note above.
  const health = useQuery<ChatHealth>({
    queryKey: ['chat', 'health'],
    queryFn: getChatHealth,
    retry: false,
  })

  const settings = useQuery<ApiKeySettings>({
    queryKey: API_KEY_SETTINGS_KEY,
    queryFn: getApiKeySettings,
    // A 403 is the answer, not a transient failure — retrying it three times
    // just delays the message telling the user to ask an administrator.
    retry: false,
  })

  function refreshDependents() {
    void qc.invalidateQueries({ queryKey: API_KEY_SETTINGS_KEY })
    // Clearing the banner is what makes the save legible. Nothing gates the
    // panel on a cached health probe — `getChatHealth` exists but has no
    // callers, and the backend reads `os.environ` at request time — so the
    // next send just works. Leaving the red "API key missing" box on screen
    // underneath a form that reported success would say otherwise.
    setError(null)
  }

  const save = useMutation({
    mutationFn: (next: string) => putApiKeySettings(next),
    onSuccess: (result) => {
      setValue('')
      refreshDependents()
      toast.success(
        result.overridden_by_environment
          ? 'Key saved, but ANTHROPIC_API_KEY is set in this process’s environment and stays in effect until it is unset.'
          : 'API key saved. The assistant is ready.',
      )
    },
  })

  const forget = useMutation({
    mutationFn: deleteApiKeySettings,
    onSuccess: () => {
      refreshDependents()
      toast.success('API key removed.')
    },
  })

  // Wait for the routing decision before rendering anything — the two
  // branches are different enough (a form vs. a deep-link) that flashing one
  // then swapping to the other would be worse than a brief nothing. Defaults
  // to `true` (builtin, i.e. today's form) once `health` SETTLES without a
  // usable `active_profile` — a real failure here must not permanently hide
  // the one feature `missing_api_key` exists to fix.
  if (health.isLoading) return null
  const isBuiltinActive = !health.data?.active_profile
    || BUILTIN_ANTHROPIC_PROFILE_IDS.has(health.data.active_profile.id)

  if (!isBuiltinActive) {
    const label = health.data?.active_profile?.label ?? 'the active model'
    return (
      <div className="mt-2" data-testid="chat-api-key-setup">
        <p className="text-[11px] text-muted">
          The &quot;{label}&quot; profile needs its own key — this form only
          covers the built-in Claude profiles. Configure it in Settings.
        </p>
        <button
          type="button"
          className="mt-1 text-[11px] underline text-muted hover:text-text"
          data-testid="chat-api-key-open-settings"
          onClick={() => {
            requestSettingsSection('assistant-model')
            setSlidePanel('settings')
          }}
        >
          Open settings
        </button>
      </div>
    )
  }

  if (settings.isLoading) return null

  if (isForbidden(settings.error)) {
    return (
      <p className="mt-2 text-[11px] text-muted" data-testid="chat-api-key-forbidden">
        Ask an administrator to add an Anthropic API key for this instance — the
        key is shared, so only a super-admin can set it.
      </p>
    )
  }

  if (settings.isError) return null

  const data = settings.data
  if (!data) return null

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const next = value.trim()
    if (!next) return
    save.mutate(next)
  }

  return (
    <div className="mt-2" data-testid="chat-api-key-setup">
      {data.configured ? (
        <p className="text-[11px] text-muted">
          A key ending {data.hint} is configured
          {data.source === 'environment' ? ' in this process’s environment' : ''}.
        </p>
      ) : (
        <p className="text-[11px] text-muted">
          Paste an Anthropic API key to enable the assistant. It is stored on
          this machine only, in {data.storage_path}.
        </p>
      )}

      <form className="mt-2 flex items-center gap-2" onSubmit={onSubmit}>
        <input
          // `type="password"` so a key pasted during a screen-share or a demo
          // is not left legible on the display afterwards.
          type="password"
          className="flex-1 bg-panel border border-border px-2 py-1 text-[11px]"
          placeholder={data.configured ? 'Replace with a new key…' : 'sk-ant-…'}
          aria-label="Anthropic API key"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          autoComplete="off"
          spellCheck={false}
          data-testid="chat-api-key-input"
        />
        <button
          type="submit"
          className="px-2 py-1 text-[11px] border border-border hover:bg-panel disabled:opacity-40"
          disabled={!value.trim() || save.isPending}
          data-testid="chat-api-key-save"
        >
          {save.isPending ? 'Saving…' : 'Save'}
        </button>
        {data.configured && data.source === 'settings' && (
          <button
            type="button"
            className="px-2 py-1 text-[11px] border border-border hover:bg-panel disabled:opacity-40"
            disabled={forget.isPending}
            onClick={() => forget.mutate()}
            data-testid="chat-api-key-forget"
          >
            Remove
          </button>
        )}
      </form>

      {data.overridden_by_environment && (
        <p className="mt-1 text-[10px] text-amber-400" data-testid="chat-api-key-masked">
          ANTHROPIC_API_KEY is also set in this process’s environment, and that
          value wins. The saved key takes over once the variable is unset.
        </p>
      )}
    </div>
  )
}
