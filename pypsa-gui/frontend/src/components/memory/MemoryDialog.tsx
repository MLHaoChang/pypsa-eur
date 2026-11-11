import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { X } from 'lucide-react'
import { Dialog } from '../Dialog'
import { Seg, Tag } from '../PageKit'
import { authApi } from '../../api/auth'
import {
  memoryApi, errorWords, KIND_WORDS, appliesToWords, conceptWords,
  type MemoryAction, type MemoryItem, type MemoryKind, type MemoryStatus,
} from '../../api/memory'
import { useAuth } from '../../auth/AuthProvider'
import { useMemoryStore, type MemoryTab } from '../../store/memoryStore'
import MemoryEditForm from './MemoryEditForm'
import { ParameterSummary, parameterBodyOf } from './ParameterMemory'
import MemoryHistory from './MemoryHistory'

// The Memory page (assistant-memory plan §4): account-level, so it opens as a
// dialog from the account menu over whatever is open, and the user never
// leaves their project or chat to reach it.

export const MEMORY_EMPTY =
  'Nothing saved yet. Use Save as default on an Asset Detail chart in Results, or say “remember this” in chat.'

const STATUS_WORDS: Record<MemoryStatus, string> = {
  proposed: 'Proposed', confirmed: 'Saved', retired: 'Retired', dismissed: 'Dismissed',
}
const STATUS_TONE: Record<MemoryStatus, 'ok' | 'neutral' | 'warn'> = {
  proposed: 'warn', confirmed: 'ok', retired: 'neutral', dismissed: 'neutral',
}

function dateWords(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  // ISO date, as elsewhere in the app (UX).
  return Number.isNaN(d.getTime()) ? '' : d.toISOString().slice(0, 10)
}

/** The Source cell: the first evidence link, as the server labels it for this
 * viewer. A project the viewer cannot open arrives without its name (M1). */
export function SourceLabel({ item }: { item: MemoryItem }) {
  const first = item.evidence[0]
  if (!first) return <span className="text-muted">Saved by hand</span>
  return (
    <span className={first.access === false ? 'text-muted italic' : ''}
      data-testid="memory-source">
      {first.label.charAt(0).toUpperCase() + first.label.slice(1)}
    </span>
  )
}

export default function MemoryDialog() {
  const open = useMemoryStore(s => s.open)
  const close = useMemoryStore(s => s.closeMemory)
  return (
    <Dialog open={open} onClose={close} title="Memory" z={9000}
      panelClassName="bg-bg rounded-lg shadow-2xl w-[960px] max-w-[96vw] h-[80vh] flex flex-col overflow-hidden border border-border">
      {open && <MemoryPanel />}
    </Dialog>
  )
}

function MemoryPanel() {
  const { user } = useAuth()
  const qc = useQueryClient()
  const tab = useMemoryStore(s => s.tab)
  const setTab = useMemoryStore(s => s.setTab)
  const editId = useMemoryStore(s => s.editId)
  const clearEdit = useMemoryStore(s => s.clearEdit)
  const close = useMemoryStore(s => s.closeMemory)

  const [kind, setKind] = useState<MemoryKind | 'all'>('all')
  const [status, setStatus] = useState<MemoryStatus | 'all'>('confirmed')
  const [scope, setScope] = useState<'all' | 'user' | 'org'>('all')
  const [editing, setEditing] = useState<string | null>(null)
  const [historyOf, setHistoryOf] = useState<string | null>(null)

  const itemsQ = useQuery({
    queryKey: ['memory', 'items', kind, status, scope],
    queryFn: () => memoryApi.list({
      kind: kind === 'all' ? undefined : kind,
      status,
      scope: scope === 'all' ? undefined : scope,
    }),
  })
  const proposalsQ = useQuery({
    queryKey: ['memory', 'proposals'],
    queryFn: () => memoryApi.list({ status: 'proposed' }),
  })
  // An item to edit may be in any status; look it up across all of them.
  const editQ = useQuery({
    queryKey: ['memory', 'items', 'all-for-edit'],
    queryFn: () => memoryApi.list({ status: 'all' }),
    enabled: !!editId,
  })
  // Suggest to workspace and the admin queue are hidden in a one-member workspace.
  const membersQ = useQuery({
    queryKey: ['org-members'],
    queryFn: authApi.orgMembers,
    staleTime: 60_000,
    retry: false,
  })
  const shared = (membersQ.data?.length ?? 0) > 1
  // An admin of the workspace may change its items (server-checked, M2); the
  // queue of members' suggestions only exists in a shared workspace.
  const isAdmin = !!itemsQ.data?.can_promote
  const canPromote = isAdmin && shared
  const suggestedQ = useQuery({
    queryKey: ['memory', 'suggested'],
    queryFn: () => memoryApi.list({ status: 'all', nominated: true }),
    enabled: canPromote,
  })

  useEffect(() => {
    if (editId) { setEditing(editId); setHistoryOf(null); clearEdit() }
  }, [editId, clearEdit])

  const act = useMutation({
    mutationFn: ({ id, action }: { id: string; action: MemoryAction }) => memoryApi.act(id, action),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['memory'] }),
    onError: err => toast.error(errorWords(err)),
  })

  const rows: MemoryItem[] = tab === 'items'
    ? itemsQ.data?.items ?? []
    : tab === 'proposals' ? proposalsQ.data?.items ?? [] : suggestedQ.data?.items ?? []
  const loading = tab === 'items' ? itemsQ.isLoading
    : tab === 'proposals' ? proposalsQ.isLoading : suggestedQ.isLoading
  const failed = tab === 'items' ? itemsQ.error
    : tab === 'proposals' ? proposalsQ.error : suggestedQ.error

  const allKnown = useMemo(() => {
    const m = new Map<string, MemoryItem>()
    for (const q of [itemsQ.data, proposalsQ.data, suggestedQ.data, editQ.data]) {
      for (const it of q?.items ?? []) m.set(it.id, it)
    }
    return m
  }, [itemsQ.data, proposalsQ.data, suggestedQ.data, editQ.data])
  const editItem = editing ? allKnown.get(editing) ?? null : null
  const proposalCount = proposalsQ.data?.items.length ?? 0

  const tabs: Array<{ value: MemoryTab; label: React.ReactNode }> = [
    { value: 'items', label: 'Items' },
    { value: 'proposals', label: proposalCount ? `Proposals · ${proposalCount}` : 'Proposals' },
  ]
  if (canPromote) tabs.push({ value: 'suggested', label: 'Suggested to workspace' })

  return (
    <>
      <div className="flex items-center gap-3 px-4 h-11 border-b border-border shrink-0">
        <h2 className="text-[13px] font-semibold text-text">Memory</h2>
        <span className="text-[10.5px] text-muted">What the assistant remembers across your projects</span>
        <div className="flex-1" />
        <button aria-label="Close" onClick={close}
          className="p-1 rounded text-muted hover:text-text hover:bg-bg-2">
          <X size={14} />
        </button>
      </div>
      <div className="flex items-center gap-2 px-4 py-2 border-b border-border shrink-0">
        <Seg value={tab} onChange={t => { setTab(t); setEditing(null); setHistoryOf(null) }}
          options={tabs} />
        {tab === 'items' && (
          <div className="flex items-center gap-2 ml-auto text-[10.5px] text-muted">
            <label className="flex items-center gap-1">Kind
              <select aria-label="Kind" value={kind} onChange={e => setKind(e.target.value as MemoryKind | 'all')}
                className="h-[22px] text-[10.5px] bg-bg border border-border rounded px-1 text-text">
                <option value="all">All</option>
                <option value="preference">{KIND_WORDS.preference}</option>
                <option value="chart_spec">{KIND_WORDS.chart_spec}</option>
                <option value="lesson">{KIND_WORDS.lesson}</option>
              </select>
            </label>
            <label className="flex items-center gap-1">Status
              <select aria-label="Status" value={status} onChange={e => setStatus(e.target.value as MemoryStatus | 'all')}
                className="h-[22px] text-[10.5px] bg-bg border border-border rounded px-1 text-text">
                <option value="confirmed">Saved</option>
                <option value="retired">Retired</option>
                <option value="dismissed">Dismissed</option>
                <option value="all">All</option>
              </select>
            </label>
            <label className="flex items-center gap-1">Scope
              <select aria-label="Scope" value={scope} onChange={e => setScope(e.target.value as 'all' | 'user' | 'org')}
                className="h-[22px] text-[10.5px] bg-bg border border-border rounded px-1 text-text">
                <option value="all">All</option>
                <option value="user">Me</option>
                <option value="org">Workspace</option>
              </select>
            </label>
          </div>
        )}
      </div>

      {editItem && (
        <MemoryEditForm
          item={editItem}
          confirmAfter={editItem.status === 'proposed'}
          onDone={() => setEditing(null)}
        />
      )}

      <div className="flex-1 overflow-auto">
        {loading ? (
          <p className="px-4 py-3 text-[11px] text-muted">Loading…</p>
        ) : failed ? (
          <p role="alert" className="px-4 py-3 text-[11px] text-danger">
            Memory could not be loaded: {errorWords(failed)}
          </p>
        ) : rows.length === 0 ? (
          <p className="px-4 py-3 text-[11px] text-muted" data-testid="memory-empty">
            {tab === 'items' ? (status === 'confirmed' || status === 'all' ? MEMORY_EMPTY : 'Nothing with this status.')
              : tab === 'proposals' ? 'No proposals waiting.'
                : 'No suggestions from workspace members.'}
          </p>
        ) : (
          <table className="w-full table-fixed text-[11px]" data-testid={`memory-table-${tab}`}>
            <colgroup>
              {['9%', '21%', '12%', '8%', '11%', '11%', '8%', '8%', '12%'].map((w, i) => (
                <col key={i} style={{ width: w }} />
              ))}
            </colgroup>
            <thead className="sticky top-0 bg-panel text-[9.5px] uppercase tracking-wide text-muted">
              <tr className="text-left">
                <th className="px-3 py-1.5 font-semibold">Kind</th>
                <th className="px-2 py-1.5 font-semibold">Summary</th>
                <th className="px-2 py-1.5 font-semibold">Applies to</th>
                <th className="px-2 py-1.5 font-semibold">Scope</th>
                <th className="px-2 py-1.5 font-semibold">Concepts</th>
                <th className="px-2 py-1.5 font-semibold">Source</th>
                <th className="px-2 py-1.5 font-semibold">Used</th>
                <th className="px-2 py-1.5 font-semibold">Status</th>
                <th className="px-3 py-1.5 font-semibold text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(item => (
                <MemoryRow key={item.id} item={item} tab={tab} shared={shared}
                  isAdmin={isAdmin} busy={act.isPending}
                  historyOpen={historyOf === item.id}
                  userId={user?.id ?? null}
                  onEdit={() => { setEditing(item.id); setHistoryOf(null) }}
                  onHistory={() => setHistoryOf(h => (h === item.id ? null : item.id))}
                  onAct={action => act.mutate({ id: item.id, action })} />
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  )
}

function MemoryRow({
  item, tab, shared, isAdmin, busy, historyOpen, userId, onEdit, onHistory, onAct,
}: {
  item: MemoryItem
  tab: MemoryTab
  shared: boolean
  isAdmin: boolean
  busy: boolean
  historyOpen: boolean
  userId: string | null
  onEdit: () => void
  onHistory: () => void
  onAct: (a: MemoryAction) => void
}) {
  const own = item.scope === 'user'
  const used = item.apply_count
    ? `${item.apply_count}× · ${dateWords(item.last_used_at)}`
    : 'Not yet'
  const param = item.kind === 'parameter' ? parameterBodyOf(item.body) : null
  return (
    <>
      <tr className="border-t border-border align-top" data-testid="memory-row" data-item-id={item.id}>
        <td className="px-3 py-1.5"><Tag tone="accent">{KIND_WORDS[item.kind]}</Tag></td>
        <td className="px-2 py-1.5 text-text">
          {param ? <ParameterSummary body={param} /> : item.summary}
        </td>
        <td className="px-2 py-1.5 text-muted">{appliesToWords(item.applies_to)}</td>
        <td className="px-2 py-1.5 text-muted">{own ? 'Just me' : 'Workspace'}</td>
        <td className="px-2 py-1.5">
          <div className="flex flex-wrap gap-1">
            {/* A parameter row names its field and class in its summary (UX P5), so its
                "<Class> setting" key is not shown again. */}
            {item.concept_keys.filter(k => !(param && k.startsWith('param:'))).map(k => (
              <span key={k}
                className="px-1 rounded bg-panel border border-border text-[9.5px] text-muted">
                {conceptWords(k)}
              </span>
            ))}
          </div>
        </td>
        <td className="px-2 py-1.5"><SourceLabel item={item} /></td>
        <td className="px-2 py-1.5 text-muted whitespace-nowrap">{used}</td>
        <td className="px-2 py-1.5">
          <div className="flex flex-col gap-0.5 items-start">
            <Tag tone={STATUS_TONE[item.status]}>{STATUS_WORDS[item.status]}</Tag>
            {own && item.nomination === 'pending' && (
              <span className="text-[9.5px] text-muted">Suggested to workspace</span>
            )}
            {own && item.nomination === 'declined' && (
              <span className="text-[9.5px] text-muted">Suggestion declined</span>
            )}
          </div>
        </td>
        <td className="px-3 py-1.5">
          <div className="flex flex-wrap justify-end gap-x-2.5 gap-y-0.5">
            {tab === 'proposals' ? (
              <>
                <Act strong disabled={busy} onClick={() => onAct('confirm')}>Confirm</Act>
                <Act disabled={busy} onClick={onEdit}>Edit and confirm</Act>
                <Act disabled={busy} onClick={() => onAct('dismiss')}>Dismiss</Act>
              </>
            ) : tab === 'suggested' ? (
              <>
                <Act strong disabled={busy} onClick={() => onAct('promote')}>Promote</Act>
                <Act disabled={busy} onClick={() => onAct('decline')}>Decline</Act>
                <Act disabled={busy} onClick={onHistory}>History</Act>
              </>
            ) : (
              <>
                {item.status === 'confirmed' && (own || isAdmin) && (
                  <Act disabled={busy} onClick={onEdit}>Edit</Act>
                )}
                {item.status === 'confirmed' && (own || isAdmin) && (
                  <Act disabled={busy} onClick={() => onAct('retire')}>Retire</Act>
                )}
                {item.status === 'retired' && (own || isAdmin) && (
                  <Act disabled={busy} onClick={() => onAct('restore')}>Restore</Act>
                )}
                {item.status !== 'dismissed' && (
                  <Act disabled={busy} onClick={onHistory}>History</Act>
                )}
                {own && shared && item.status === 'confirmed' && !item.nomination && (
                  <Act disabled={busy} onClick={() => onAct('nominate')}>Suggest to workspace</Act>
                )}
              </>
            )}
          </div>
        </td>
      </tr>
      {historyOpen && (
        <tr className="border-t border-border bg-panel/50">
          <td colSpan={9} className="px-3 py-2">
            <MemoryHistory itemId={item.id} userId={userId} />
          </td>
        </tr>
      )}
    </>
  )
}

/** A row action: a text button, so a row stays one line (DENSITY-1). */
function Act({ strong, children, ...props }: { strong?: boolean } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button type="button" {...props}
      className={`text-[10.5px] whitespace-nowrap hover:underline disabled:opacity-40 disabled:cursor-not-allowed ${
        strong ? 'font-semibold text-accent' : 'text-ink-700 hover:text-text'}`}>
      {children}
    </button>
  )
}
