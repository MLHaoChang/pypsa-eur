/**
 * Chat harness issue 19, slice 2 — the super-admin surface for MCP servers
 * (`.scratch/harness/mcp-spec.md` A.1): the list, the enable toggle, each
 * server's keys, the connection test and add/edit/delete.
 *
 * Only http servers are added, edited, toggled or deleted here. A stdio
 * server is a program this machine runs, so it is written by hand in
 * `mcp-servers.json`; its row is read-only (the backend refuses to change it)
 * but can be tested and keyed.
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
/** The server's own tool name: the model sees `mcp__<server>__<tool>`. */
const shortToolName = (name: string) => name.replace(/^mcp__[a-z0-9]+__/, '')

const INPUT = 'w-full rounded border border-border bg-bg px-2 py-1 text-xs'
const BUTTON = 'rounded border border-border px-2 py-1 text-xs disabled:opacity-50'

type TestState = 'running' | 'error' | McpTestResult

function bodyOf(server: McpServerOut, enabled = server.enabled): McpServerIn {
  return { enabled, url: server.url ?? '', tiers: server.tiers as Record<string, McpTier> }
}

/** Why a row's toggle cannot change, or null when it can. */
function lockedReason(server: McpServerOut): string | null {
  if (server.id == null || server.problem) return 'Fix this entry in mcp-servers.json'
  if (server.transport !== 'http') return 'Command servers are edited in mcp-servers.json'
  return null
}

function target(server: McpServerOut): string {
  return server.transport === 'http' ? server.url ?? '' : [server.command, ...server.args].join(' ')
}

function rowText(server: McpServerOut): string {
  return `${server.id ?? '(invalid entry)'} · ${server.transport ?? 'unknown'} · ${target(server)}`
}

function tierWords(tier: McpTier): string {
  return tier === 'read' ? 'Read only' : 'Asks first'
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
  const [replacing, setReplacing] = useState(false)
  const editing = !status.present || replacing
  return (
    <div className="space-y-1" data-testid={testId}>
      <div className="flex items-center gap-2 text-xs">
        <span className="w-40 shrink-0 font-mono truncate" title={label}>{label}</span>
        <span className={status.present ? 'shrink-0' : 'shrink-0 text-muted'} data-testid={`${testId}-status`}>
          {status.present ? `Key set${status.hint ? ` (${status.hint})` : ''}` : 'No key'}
        </span>
        {editing && (
          <>
            <input
              type="password"
              autoComplete="off"
              className={INPUT}
              placeholder="Paste the value"
              aria-label={`${label} value`}
              value={draft}
              onChange={e => setDraft(e.target.value)}
              data-testid={`${testId}-input`}
            />
            <button
              type="button"
              className={BUTTON}
              disabled={pending || !draft}
              onClick={() => { onSave(draft); setDraft(''); setReplacing(false) }}
              data-testid={`${testId}-save`}
            >
              Save
            </button>
          </>
        )}
        {status.present && !replacing && (
          <button type="button" className="text-xs text-muted underline" onClick={() => setReplacing(true)}
            data-testid={`${testId}-replace`}>
            Replace
          </button>
        )}
        {replacing && (
          <button type="button" className="text-xs text-muted underline"
            onClick={() => { setDraft(''); setReplacing(false) }} data-testid={`${testId}-cancel`}>
            Cancel
          </button>
        )}
        {status.present && (
          <button type="button" className="text-xs text-muted underline" onClick={onClear}
            data-testid={`${testId}-clear`}>
            Remove
          </button>
        )}
      </div>
      {status.redactable === false && (
        <p className="text-xs text-warn" data-testid={`${testId}-short`}>Too short to hide from logs</p>
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
        Read-only tools run without asking; every other tool asks first.
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
              <td className="pr-2 font-mono break-all">{shortToolName(t.name)}</td>
              <td className="pr-2 whitespace-nowrap" title={t.tier}>{tierWords(t.tier)}</td>
              <td>
                <pre className="whitespace-pre-wrap break-words font-mono text-[11px] text-muted bg-bg-2 rounded p-1">
                  {t.description}
                </pre>
              </td>
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
  const [url, setUrl] = useState(initial?.url ?? '')
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
  const ready = ID_RE.test(id) && !idError && !tierError && url.trim() !== ''

  function submit() {
    onSubmit(id, {
      enabled: initial?.enabled ?? false,
      url: url.trim(),
      tiers: parsedTiers as Record<string, McpTier>,
    })
  }

  return (
    <div className="rounded border border-border p-3 space-y-2" data-testid="mcp-server-form">
      <label className="block text-xs text-muted" htmlFor="mcp-form-id">Server id</label>
      <input
        id="mcp-form-id"
        className={INPUT}
        placeholder="e.g. tariffs"
        value={id}
        disabled={initial != null}
        onChange={e => setId(e.target.value)}
        data-testid="mcp-form-id"
      />
      {idError && <p className="text-xs text-danger">{idError}</p>}
      <label className="block text-xs text-muted" htmlFor="mcp-form-url">URL</label>
      <input id="mcp-form-url" className={INPUT} placeholder="https://…/mcp (http only on this machine)"
        value={url} onChange={e => setUrl(e.target.value)} data-testid="mcp-form-url" />
      <p className="text-xs text-muted">
        URL servers only. A server that runs as a command on this machine is added by editing
        mcp-servers.json by hand.
      </p>
      <textarea className={INPUT} rows={2} aria-label="Tier overrides"
        placeholder="Optional tier overrides, one per line: tool = destructive (can only raise a tier)"
        value={tiers} onChange={e => setTiers(e.target.value)} data-testid="mcp-form-tiers" />
      {tierError && <p className="text-xs text-danger">{tierError}</p>}
      {!ready && !idError && !tierError && (
        <p className="text-xs text-muted" data-testid="mcp-form-missing">
          {ID_RE.test(id) ? 'Enter the server URL to save.' : 'Enter a server id to save.'}
        </p>
      )}
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
        <p className="rounded border border-warn/40 bg-warn/5 p-2 text-xs mt-1" data-testid="mcp-warning">
          Add only servers you trust: their tool descriptions and results reach the model, and the assistant
          asks before every tool a server does not mark read-only.
        </p>
        <p className="text-xs text-muted mt-1">
          Tools from these servers join the assistant&apos;s catalogue for every model, starting with the next chat.
        </p>
      </div>
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
              <input
                type="checkbox"
                checked={s.enabled}
                disabled={lockedReason(s) != null || saveMutation.isPending}
                onChange={e => s.id && saveMutation.mutate({ id: s.id, body: bodyOf(s, e.target.checked) })}
                aria-label={`Enable ${s.id ?? 'server'}`}
                aria-describedby={lockedReason(s) ? `mcp-locked-${index}` : undefined}
                data-testid={`mcp-enable-${s.id}`}
              />
              <span className="min-w-0 flex-1 truncate" title={rowText(s)} data-testid={`mcp-row-${s.id}`}>
                <span className="font-semibold">{s.id ?? '(invalid entry)'}</span>
                <span className="text-muted"> · {s.transport ?? 'unknown'} · </span>
                <span className="font-mono">{target(s)}</span>
              </span>
              {s.id && (
                <button type="button" className={BUTTON} onClick={() => void runTest(s.id!)}
                  disabled={tests[s.id] === 'running' || s.problem != null}
                  aria-label={`Test ${s.id}`} data-testid={`mcp-test-${s.id}`}>
                  Test
                </button>
              )}
              {s.id && s.transport === 'http' && s.problem == null && (
                <>
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
            {lockedReason(s) && (
              <p id={`mcp-locked-${index}`} className="text-xs text-muted" data-testid={`mcp-locked-${s.id}`}>
                {lockedReason(s)}
              </p>
            )}
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
        confirmLabel="Delete server"
        danger
        pending={deleteMutation.isPending}
        onConfirm={() => deleteTarget && deleteMutation.mutate(deleteTarget)}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  )
}
