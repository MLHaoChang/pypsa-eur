/**
 * Desktop-only Settings pane: the Anthropic API key and the application log.
 *
 * Renders nothing at all when `fetchLocalSettings` returns null — the routes
 * 404 on a web deployment, and one build serves both.
 */
import { useState } from 'react'
import toast from 'react-hot-toast'
import { confirmToast } from '../utils/toasts'
import { useInvalidateLocalSettings, useLocalSettings } from '../hooks/useLocalSettings'
import {
  keyFieldPlaceholder,
  probeMessage,
  putApiKey,
  revealLog,
  type ProbeMessage,
} from '../api/localSettings'

// Tokens defined in src/index.css:71-73 (--color-success / --color-warn /
// --color-danger). There is no `text-ok`.
const TONE_CLASS: Record<ProbeMessage['tone'], string> = {
  ok: 'text-success',
  warn: 'text-warn',
  error: 'text-danger',
}

export default function LocalSettings() {
  const { data: state, isLoading } = useLocalSettings()
  const invalidate = useInvalidateLocalSettings()
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<ProbeMessage | null>(null)

  if (isLoading) return <div className="p-4 text-muted">Loading…</div>
  // null means the routes 404: this build is not the desktop app.
  if (state == null) return null

  const save = async (value: string) => {
    setBusy(true)
    try {
      const result = await putApiKey(value)
      setMessage(probeMessage(result.status))
      setDraft('')
      await invalidate()
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
    confirmToast(
      'Remove the stored Anthropic API key? Chat will be disabled.',
      () => save(''),
      { confirmLabel: 'Remove', danger: true },
    )
  }

  const reveal = async () => {
    const result = await revealLog()
    if (!result.revealed) {
      toast.error(`Could not open the file manager. The log is at ${result.log_path}`)
    }
  }

  const copyPath = async () => {
    if (!state) return
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
    <div className="p-4 space-y-6 overflow-y-auto">
      <section className="space-y-2">
        <h3 className="text-sm font-semibold">Anthropic API key</h3>
        <p className="text-xs text-muted">
          Needed for the chat assistant. Stored on this machine only, in your
          application data folder — never in a project file.
        </p>
        <div className="flex gap-2">
          <input
            type="password"
            className="flex-1 rounded border border-border bg-surface px-2 py-1 text-sm"
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
        <code className="block break-all rounded bg-surface px-2 py-1 text-xs">
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
    </div>
  )
}
