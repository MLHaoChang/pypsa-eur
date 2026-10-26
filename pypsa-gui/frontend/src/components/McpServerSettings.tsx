/**
 * Chat harness issue 19, slice 2 — the super-admin surface for MCP servers
 * (`.scratch/harness/mcp-spec.md` A.1): the list, the enable toggle, each
 * server's keys, the connection test and add/edit/delete.
 *
 * HOSTING. Rendered from pages/LocalSettings.tsx under AssistantModelSettings
 * and, like it, above that pane's local-only early return: the routes are
 * super-admin gated, not desktop-only. Same four renders as
 * AssistantModelSettings: loading → nothing, error → a visible outage with
 * retry, 403/404 → nothing, ready → the list.
 *
 * SECURITY. Key inputs start and reset to '' and are never seeded from the
 * hint. Test output (tool names, descriptions) is server-supplied text and
 * rendered as plain text only; a failed test shows the backend's fixed
 * message for its kind, never the server's own error.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'

import {
  deleteMcpKey,
  deleteMcpServer,
  fetchMcpServersOrNull,
  postMcpTest,
  putMcpKey,
  putMcpServer,
  type McpKeyStatus,
  type McpServerIn,
  type McpServerOut,
  type McpTestResult,
  type McpTier,
} from '../api/mcpSettings'
import { ConfirmDialog } from './ConfirmDialog'

export const MCP_SERVERS_KEY = ['mcpServers'] as const

const ID_RE = /^[a-z0-9]{1,16}$/
const TIERS: McpTier[] = ['read', 'write', 'destructive', 'execution', 'execution_long_running']
const INPUT = 'w-full rounded border border-border bg-bg px-2 py-1 text-xs'
const BUTTON = 'rounded border border-border px-2 py-1 text-xs disabled:opacity-50'

type TestState = 'running' | 'error' | McpTestResult

function bodyOf(server: McpServerOut, enabled = server.enabled): McpServerIn {
  return {
    enabled,
    transport: server.transport ?? 'stdio',
    command: server.command,
    args: server.args,
    url: server.url,
    env: server.env.map(e => e.name),
    tiers: server.tiers as Record<string, McpTier>,
  }
}

function errorText(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return typeof detail === 'string' ? detail : fallback
}

function KeyField({
  label, status, testId, pending, onSave, onClear,
}: {
  label: string
  status: McpKeyStatus
  testId: string
  pending: boolean
  onSave: (value: string) => void
  onClear: () => void
}) {
  const [draft, setDraft] = useState('')
  return (
    <div className="flex items-center gap-2 text-xs" data-testid={testId}>
      <span className="w-40 shrink-0 font-mono truncate" title={label}>{label}</span>
      <input
        type="password"
        autoComplete="off"
        className={INPUT}
        placeholder={status.present ? `Stored (${status.hint ?? 'set'}); type to replace` : 'Not set'}
        aria-label={`${label} value`}
        value={draft}
        onChange={e => setDraft(e.target.value)}
        data-testid={`${testId}-input`}
      />
      <button
        type="button"
        className={BUTTON}
        disabled={pending || !draft}
        onClick={() => { onSave(draft); setDraft('') }}
        data-testid={`${testId}-save`}
      >
        Save
      </button>
      {status.present && (
        <button type="button" className="text-xs text-muted underline" onClick={onClear}
          data-testid={`${testId}-clear`}>
          Remove
        </button>
      )}
      {status.redactable === false && (
        <span className="text-warn" title="Too short to be hidden from logs and transcripts">short</span>
      )}
    </div>
  )
}

function TestOutput({ state }: { state: TestState }) {
  if (state === 'running') return <p className="text-xs text-muted">Connecting…</p>
  if (state === 'error') return <p className="text-xs text-danger">The test could not be run.</p>
  if (!state.ok) {
    return <p className="text-xs text-danger" data-testid="mcp-test-failed">{state.message}</p>
  }
  return (
    <div className="space-y-1" data-testid="mcp-test-tools">
      <p className="text-xs text-muted">
        {state.tools.length} tool{state.tools.length === 1 ? '' : 's'} offered to the model
        {state.skipped > 0 && `, ${state.skipped} skipped (name, schema or collision)`}.
        Tools marked read run without asking; every other tier asks first.
      </p>
      <table className="w-full text-xs">
        <thead>
          <tr className="text-left text-muted">
            <th scope="col" className="pr-2 font-normal">Tool</th>
            <th scope="col" className="pr-2 font-normal">Tier</th>
            <th scope="col" className="font-normal">Description the model reads</th>
          </tr>
        </thead>
        <tbody>
          {state.tools.map(t => (
            <tr key={t.name} className="align-top border-t border-border">
              <td className="pr-2 font-mono break-all">{t.name}</td>
              <td className="pr-2">{t.tier}</td>
              <td className="break-words">{t.description}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ServerForm({
  initial, takenIds, pending, onSubmit, onCancel,
}: {
  initial: McpServerOut | null
  takenIds: Set<string>
  pending: boolean
  onSubmit: (id: string, body: McpServerIn) => void
  onCancel: () => void
}) {
  const [id, setId] = useState(initial?.id ?? '')
  const [transport, setTransport] = useState<'stdio' | 'http'>(initial?.transport ?? 'stdio')
  const [command, setCommand] = useState(initial?.command ?? '')
  const [args, setArgs] = useState((initial?.args ?? []).join('\n'))
  const [url, setUrl] = useState(initial?.url ?? '')
  const [env, setEnv] = useState((initial?.env ?? []).map(e => e.name).join(', '))
  const [tiers, setTiers] = useState(
    Object.entries(initial?.tiers ?? {}).map(([tool, tier]) => `${tool} = ${tier}`).join('\n'),
  )

  const idError = initial == null && id
    ? (!ID_RE.test(id) ? 'Use 1–16 lowercase letters or digits.' : takenIds.has(id) ? 'That id is taken.' : null)
    : null
  const parsedTiers: Record<string, string> = {}
  let tierError: string | null = null
  for (const line of tiers.split('\n').map(l => l.trim()).filter(Boolean)) {
    const [tool, tier] = line.split('=').map(s => s.trim())
    if (!tool || !TIERS.includes(tier as McpTier)) {
      tierError = `"${line}": write tool = ${TIERS.join(' | ')}`
      break
    }
    parsedTiers[tool] = tier
  }
  const ready = ID_RE.test(id) && !idError && !tierError &&
    (transport === 'stdio' ? command.trim() !== '' : url.trim() !== '')

  function submit() {
    onSubmit(id, {
      enabled: initial?.enabled ?? false,
      transport,
      command: transport === 'stdio' ? command.trim() : null,
      args: transport === 'stdio' ? args.split('\n').map(a => a.trim()).filter(Boolean) : [],
      url: transport === 'http' ? url.trim() : null,
      env: env.split(',').map(v => v.trim()).filter(Boolean),
      tiers: parsedTiers as Record<string, McpTier>,
    })
  }

  return (
    <div className="rounded border border-border p-3 space-y-2" data-testid="mcp-server-form">
      <div className="flex gap-2">
        <input
          className={INPUT}
          placeholder="Server id (e.g. tariffs)"
          aria-label="Server id"
          value={id}
          disabled={initial != null}
          onChange={e => setId(e.target.value)}
          data-testid="mcp-form-id"
        />
        <select
          className="rounded border border-border bg-bg px-2 py-1 text-xs"
          aria-label="Transport"
          value={transport}
          onChange={e => setTransport(e.target.value as 'stdio' | 'http')}
          data-testid="mcp-form-transport"
        >
          <option value="stdio">Local command (stdio)</option>
          <option value="http">URL (Streamable HTTP)</option>
        </select>
      </div>
      {idError && <p className="text-xs text-danger">{idError}</p>}
      {transport === 'stdio' ? (
        <>
          <input className={INPUT} placeholder="Command (full path)" aria-label="Command"
            value={command} onChange={e => setCommand(e.target.value)} data-testid="mcp-form-command" />
          <textarea className={INPUT} rows={2} placeholder="Arguments, one per line" aria-label="Arguments"
            value={args} onChange={e => setArgs(e.target.value)} data-testid="mcp-form-args" />
          <input className={INPUT} placeholder="Variables the server needs, comma-separated (e.g. TARIFF_TOKEN)"
            aria-label="Environment variables" value={env} onChange={e => setEnv(e.target.value)}
            data-testid="mcp-form-env" />
          <p className="text-xs text-muted">
            The server process sees only these variables (set their values after saving) plus PATH, HOME and
            the locale, never the app&apos;s own keys.
          </p>
        </>
      ) : (
        <input className={INPUT} placeholder="https://…/mcp (http only on this machine)" aria-label="URL"
          value={url} onChange={e => setUrl(e.target.value)} data-testid="mcp-form-url" />
      )}
      <textarea className={INPUT} rows={2} aria-label="Tier overrides"
        placeholder="Optional tier overrides, one per line: tool = destructive (can only raise a tier)"
        value={tiers} onChange={e => setTiers(e.target.value)} data-testid="mcp-form-tiers" />
      {tierError && <p className="text-xs text-danger">{tierError}</p>}
      <div className="flex items-center gap-2">
        <button type="button" className={BUTTON} disabled={pending || !ready} onClick={submit}
          data-testid="mcp-form-submit">
          {pending ? 'Saving…' : 'Save server'}
        </button>
        <button type="button" className="text-xs text-muted underline" onClick={onCancel}
          data-testid="mcp-form-cancel">
          Cancel
        </button>
      </div>
    </div>
  )
}

export default function McpServerSettings() {
  const qc = useQueryClient()
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: MCP_SERVERS_KEY,
    queryFn: fetchMcpServersOrNull,
    staleTime: Infinity,
    retry: 2,
  })
  const [form, setForm] = useState<'add' | McpServerOut | null>(null)
  const [tests, setTests] = useState<Record<string, TestState>>({})
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const refresh = () => qc.invalidateQueries({ queryKey: MCP_SERVERS_KEY })

  const saveMutation = useMutation({
    mutationFn: (v: { id: string; body: McpServerIn }) => putMcpServer(v.id, v.body),
    onSuccess: (_r, v) => {
      setForm(null)
      setTests(t => { const { [v.id]: _drop, ...rest } = t; return rest })
      void refresh()
    },
    onError: e => toast.error(errorText(e, 'Could not save the server.')),
  })
  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteMcpServer(id),
    onSuccess: () => { setDeleteTarget(null); void refresh() },
    onError: () => toast.error('Could not delete the server.'),
  })
  const keyMutation = useMutation({
    mutationFn: (v: { id: string; name: string; value: string | null }) =>
      v.value == null ? deleteMcpKey(v.id, v.name) : putMcpKey(v.id, v.name, v.value),
    onSuccess: () => void refresh(),
    onError: e => toast.error(errorText(e, 'Could not save the key.')),
  })

  async function runTest(id: string) {
    setTests(t => ({ ...t, [id]: 'running' }))
    try {
      const result = await postMcpTest(id)
      setTests(t => ({ ...t, [id]: result }))
    } catch {
      setTests(t => ({ ...t, [id]: 'error' }))
    }
  }

  if (isLoading) return null
  if (isError) {
    return (
      <div data-testid="mcp-settings-error"
        className="rounded border border-danger/40 bg-danger/5 p-3 space-y-2 text-xs text-danger">
        <p>Could not load the MCP server settings.</p>
        <button type="button" className="underline" onClick={() => void refetch()}>Retry</button>
      </div>
    )
  }
  if (data == null) return null

  const takenIds = new Set(data.servers.map(s => s.id).filter((s): s is string => s != null))

  return (
    <div id="mcp-servers" data-testid="mcp-settings" className="space-y-3">
      <div>
        <h3 className="text-sm font-semibold">External tools (MCP servers)</h3>
        <p className="text-xs text-muted">
          Tools from these servers join the assistant&apos;s catalogue for every model, starting with the next chat.
        </p>
      </div>
      <p className="rounded border border-warn/40 bg-warn/5 p-2 text-xs" data-testid="mcp-warning">
        A server&apos;s tool descriptions and results reach the model, and a local server runs as a program on
        this machine. Add only servers you trust. Unless a server marks a tool read-only, the assistant asks
        before every call to it, in Expert and Guided alike.
      </p>
      {data.file_error && (
        <p className="text-xs text-danger" data-testid="mcp-file-error">
          {data.file_error}. Fix or remove the file; nothing is written while it is unreadable.
        </p>
      )}

      {data.servers.length === 0 && !data.file_error && (
        <p className="text-xs text-muted" data-testid="mcp-empty">No servers. External tools are off.</p>
      )}
      <ul className="space-y-2">
        {data.servers.map((s, index) => (
          <li key={s.id ?? `invalid-${index}`} className="rounded border border-border p-2 space-y-2"
            data-testid={`mcp-server-${s.id ?? 'invalid'}`}>
            <div className="flex items-center gap-2 text-xs">
              <label className="flex items-center gap-1">
                <input
                  type="checkbox"
                  checked={s.enabled}
                  disabled={s.id == null || saveMutation.isPending}
                  onChange={e => s.id && saveMutation.mutate({ id: s.id, body: bodyOf(s, e.target.checked) })}
                  aria-label={`Enable ${s.id ?? 'server'}`}
                  data-testid={`mcp-enable-${s.id}`}
                />
                <span className="font-semibold">{s.id ?? '(invalid entry)'}</span>
              </label>
              <span className="text-muted">{s.transport === 'http' ? 'URL' : 'command'}</span>
              <span className="font-mono truncate min-w-0 flex-1"
                title={s.transport === 'http' ? s.url ?? '' : [s.command, ...s.args].join(' ')}>
                {s.transport === 'http' ? s.url : [s.command, ...s.args].join(' ')}
              </span>
              {s.id && (
                <>
                  <button type="button" className={BUTTON} onClick={() => void runTest(s.id!)}
                    disabled={tests[s.id] === 'running'} aria-label={`Test ${s.id}`} data-testid={`mcp-test-${s.id}`}>
                    Test
                  </button>
                  <button type="button" className="text-xs text-muted underline" onClick={() => setForm(s)}
                    aria-label={`Edit ${s.id}`} data-testid={`mcp-edit-${s.id}`}>
                    Edit
                  </button>
                  <button type="button" className="text-xs text-danger underline" onClick={() => setDeleteTarget(s.id)}
                    aria-label={`Delete ${s.id}`} data-testid={`mcp-delete-${s.id}`}>
                    Delete
                  </button>
                </>
              )}
            </div>
            {s.problem && <p className="text-xs text-danger">{s.problem}</p>}
            {s.id && s.key && (
              <KeyField label="Bearer token" status={s.key} testId={`mcp-key-${s.id}-bearer`}
                pending={keyMutation.isPending}
                onSave={value => keyMutation.mutate({ id: s.id!, name: 'bearer', value })}
                onClear={() => keyMutation.mutate({ id: s.id!, name: 'bearer', value: null })} />
            )}
            {s.id && s.env.map(e => (
              <KeyField key={e.name} label={e.name} status={e} testId={`mcp-key-${s.id}-${e.name}`}
                pending={keyMutation.isPending}
                onSave={value => keyMutation.mutate({ id: s.id!, name: e.name, value })}
                onClear={() => keyMutation.mutate({ id: s.id!, name: e.name, value: null })} />
            ))}
            {s.id && tests[s.id] && <TestOutput state={tests[s.id]} />}
          </li>
        ))}
      </ul>

      {form == null ? (
        <button type="button" className="text-xs underline text-muted hover:text-text"
          onClick={() => setForm('add')} disabled={data.file_error != null} data-testid="mcp-add-open">
          Add server
        </button>
      ) : (
        <ServerForm
          key={form === 'add' ? 'add' : form.id ?? 'edit'}
          initial={form === 'add' ? null : form}
          takenIds={takenIds}
          pending={saveMutation.isPending}
          onCancel={() => setForm(null)}
          onSubmit={(id, body) => saveMutation.mutate({ id, body })}
        />
      )}

      <ConfirmDialog
        open={deleteTarget != null}
        title="Delete MCP server"
        message={deleteTarget
          ? `Delete "${deleteTarget}" and its stored keys? Its tools leave the assistant from the next chat.`
          : ''}
        confirmLabel="Delete"
        danger
        pending={deleteMutation.isPending}
        onConfirm={() => deleteTarget && deleteMutation.mutate(deleteTarget)}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  )
}
