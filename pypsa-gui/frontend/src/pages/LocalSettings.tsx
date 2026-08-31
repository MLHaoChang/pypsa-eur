/**
 * The Settings pane: the desktop-only Anthropic API key + application log,
 * AND (Task 15) the AssistantModelSettings section, which is NOT
 * desktop-only — it is super-admin-gated and meaningful on a server too.
 *
 * The two are independently gated and either can be absent without the
 * other: `AssistantModelSettings` hides itself when `/chat/settings/llm` is
 * unreachable (see hooks/useLLMSettings.ts), and the local-settings body
 * below still hides on `state == null` (`/api/local-settings` 404s on a web
 * deployment). AssistantModelSettings is hosted ABOVE that null check —
 * NOT inside this component's own early return — precisely so a web
 * deployment (local-settings unreachable) still renders it for a
 * super-admin.
 *
 * The pane itself renders `null` only once BOTH surfaces have confirmed
 * unreachable — and waits for the llm-settings query to SETTLE (not just
 * "not yet true") before deciding that, so a slow-but-eventually-reachable
 * llm-settings fetch cannot flash "nothing" and then pop the section back
 * in a moment later. "Confirmed unreachable" is NOT the same as "errored"
 * (fix round 1): an llm-settings OUTAGE (`isError`) is excluded from that
 * null-return on purpose, so `AssistantModelSettings`' own visible outage
 * state (see its header) still gets to mount even when local-settings is
 * also unavailable — an admin-facing surface silently vanishing during a
 * real failure is the exact thing ADR-0001 rules out.
 */
import { useState } from 'react'
import toast from 'react-hot-toast'
import { confirmToast } from '../utils/toasts'
import { useInvalidateLocalSettings, useLocalSettings } from '../hooks/useLocalSettings'
import { useLLMSettings } from '../hooks/useLLMSettings'
import {
  keyFieldPlaceholder,
  probeMessage,
  putApiKey,
  revealLog,
  type LocalSettingsState,
  type ProbeMessage,
} from '../api/localSettings'
import AssistantModelSettings from '../components/AssistantModelSettings'

// Tokens defined in src/index.css:71-73 (--color-success / --color-warn /
// --color-danger). There is no `text-ok`.
const TONE_CLASS: Record<ProbeMessage['tone'], string> = {
  ok: 'text-success',
  warn: 'text-warn',
  error: 'text-danger',
}

/**
 * The desktop-only key + diagnostics body. Split out from `LocalSettings` so
 * `state` is a plain, non-null `LocalSettingsState` prop rather than a
 * closed-over nullable — the parent only ever mounts this once `state` is
 * known non-null, and this keeps that guarantee visible in the type rather
 * than relying on call-site discipline.
 */
function LocalSettingsBody({
  state, invalidate,
}: {
  state: LocalSettingsState
  invalidate: () => Promise<void>
}) {
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<ProbeMessage | null>(null)

  const save = async (value: string) => {
    setBusy(true)
    try {
      const result = await putApiKey(value)
      setMessage(probeMessage(result.status))
      setDraft('')
      await invalidate()
    } catch {
      // The axios interceptor already toasts on a transport failure (this
      // call carries no skipErrorToast), so the user is informed either way.
      // Catch only to keep the rejection from surfacing as unhandled-promise
      // console noise — nothing else changes for the user.
    } finally {
      setBusy(false)
    }
  }

  const clear = () => {
    // `confirmToast`, NOT window.confirm. src/utils/toasts.tsx:4 records why:
    // native dialogs block the main thread, cannot be styled, and are
    // "bypassed in CI / headless setups, silently auto-cancelling" — which is
    // exactly what a WKWebView with no JS-dialog delegate would do, turning
    // Clear into a button that does nothing.
    //
    // "The built-in Claude profiles" — NOT "chat" — because this key is
    // ANTHROPIC_API_KEY specifically (see api/localSettings.ts's header): a
    // different active LLM profile (Task 15 added several) would keep
    // working after this key is cleared.
    confirmToast(
      'Remove the stored Anthropic API key? The built-in Claude Sonnet / Claude Opus profiles will stop working until a new key is set.',
      () => save(''),
      { confirmLabel: 'Remove', danger: true },
    )
  }

  const reveal = async () => {
    // `revealLog()` passes skipErrorToast: true (the backend's own failure
    // path already returns 200 + revealed:false, handled below), so a
    // transport-level failure (network drop, backend not reachable) reaches
    // here as a rejected promise with no toast anywhere else in the chain.
    // Without this catch, the button would just do nothing — the exact dead
    // end the design says this feature must degrade away from.
    try {
      const result = await revealLog()
      if (!result.revealed) {
        toast.error(`Could not open the file manager. The log is at ${result.log_path}`)
      }
    } catch {
      toast.error(`Could not reach the app to reveal the log. It is at ${state.log_path}`)
    }
  }

  const copyPath = async () => {
    // navigator.clipboard is undefined outside a secure context. The desktop
    // app serves from 127.0.0.1 (which qualifies), but a plain-http web
    // deployment does not — and a thrown TypeError here would surface as a
    // dead button rather than a message.
    try {
      await navigator.clipboard.writeText(state.log_path)
      toast.success('Log path copied')
    } catch {
      toast.error('Could not copy — select the path above instead.')
    }
  }

  return (
    <>
      <section className="space-y-2">
        <h3 className="text-sm font-semibold">Anthropic API key</h3>
        <p className="text-xs text-muted">
          Needed for the built-in Claude Sonnet / Claude Opus profiles. Stored
          on this machine only, in your application data folder — never in a
          project file.
        </p>
        <div className="flex gap-2">
          <input
            type="password"
            className="flex-1 rounded border border-border bg-bg px-2 py-1 text-sm text-text focus:outline-none focus:border-accent"
            placeholder={keyFieldPlaceholder(state)}
            value={draft}
            onChange={e => setDraft(e.target.value)}
            autoComplete="off"
          />
          <button
            className="rounded border border-border px-3 py-1 text-sm disabled:opacity-50"
            disabled={busy || draft.trim() === ''}
            onClick={() => save(draft)}
          >
            Save
          </button>
          {state.key_set && (
            <button
              className="rounded border border-border px-3 py-1 text-sm disabled:opacity-50"
              disabled={busy}
              onClick={clear}
            >
              Clear
            </button>
          )}
        </div>
        {message && (
          <p className={`text-xs ${TONE_CLASS[message.tone]}`}>{message.text}</p>
        )}
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold">Diagnostics</h3>
        <p className="text-xs text-muted">
          Errors the app cannot show you land here. Include this file when
          reporting a problem.
        </p>
        <code className="block break-all rounded bg-panel border border-border px-2 py-1 text-xs text-text">
          {state.log_path}
        </code>
        <div className="flex gap-2">
          <button className="rounded border border-border px-3 py-1 text-sm" onClick={reveal}>
            Reveal in file manager
          </button>
          <button className="rounded border border-border px-3 py-1 text-sm" onClick={copyPath}>
            Copy path
          </button>
        </div>
      </section>
    </>
  )
}

export default function LocalSettings() {
  const { data: state, isLoading } = useLocalSettings()
  const llmSettings = useLLMSettings()
  const invalidate = useInvalidateLocalSettings()

  if (isLoading) return <div className="p-4 text-muted">Loading…</div>
  // Both surfaces confirmed UNAVAILABLE (not merely erroring) — this pane
  // truly has nothing to show. `llmSettings.data == null` alone is NOT
  // enough (fix round 1): that is also true while `llmSettings.isError` —
  // a genuine outage, not "not for you" — and `AssistantModelSettings`
  // renders its OWN visible error state for that case (see its header and
  // hooks/useLLMSettings.ts). Returning null here on `isError` would bury
  // that state before it ever mounts, hiding a real outage behind "this
  // pane doesn't exist" — exactly the ADR-0001 violation this fix closes.
  if (state == null && !llmSettings.isLoading && !llmSettings.isError && llmSettings.data == null) {
    return null
  }

  return (
    <div className="p-4 space-y-6 overflow-y-auto">
      <AssistantModelSettings />
      {state != null && <LocalSettingsBody state={state} invalidate={invalidate} />}
    </div>
  )
}
