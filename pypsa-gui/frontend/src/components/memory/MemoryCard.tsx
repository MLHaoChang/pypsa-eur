import { useState } from 'react'
import toast from 'react-hot-toast'
import {
  memoryApi, errorWords, KIND_WORDS, appliesToWords, conceptWords,
  type MemoryKind, type NewMemoryItem, type ProjectKind,
} from '../../api/memory'
import { useChatStore } from '../../store/chatStore'
import { useMemoryStore } from '../../store/memoryStore'

// The memory card (plan §2.2): what `propose_memory` would save, answered with
// Remember · Edit first · Not now. Same layout in Guided and Expert. Remember
// approves the card (the tool writes the item); Not now denies it and records
// a dismissal, so the same proposal is not offered again for 30 days.

export const MEMORY_TOOL = 'propose_memory'

export interface MemoryProposal {
  kind: MemoryKind
  summary: string
  body: Record<string, unknown>
  concept_keys: string[]
  project_kind: ProjectKind | null
}

/** The card's args as a proposal, or null when they are not one. */
export function memoryProposalOf(args: unknown): MemoryProposal | null {
  if (!args || typeof args !== 'object') return null
  const a = args as Record<string, unknown>
  if (typeof a.kind !== 'string' || !(a.kind in KIND_WORDS)) return null
  if (typeof a.summary !== 'string') return null
  if (!a.body || typeof a.body !== 'object' || Array.isArray(a.body)) return null
  if (!Array.isArray(a.concept_keys) || !a.concept_keys.every(k => typeof k === 'string')) return null
  return {
    kind: a.kind as MemoryKind,
    summary: a.summary,
    body: a.body as Record<string, unknown>,
    concept_keys: a.concept_keys as string[],
    project_kind: (typeof a.project_kind === 'string' ? a.project_kind : null) as ProjectKind | null,
  }
}

/** Resolve with the saved item's id once the tool's result reaches the
 * transcript, or null after `ms` (a strict-assurance turn carries no result). */
export function savedItemId(toolUseId: string, ms = 20_000): Promise<string | null> {
  const find = () => {
    const msg = useChatStore.getState().messages.find(
      m => m.role === 'tool' && m.tool_use_id === toolUseId && m.tool_result != null,
    )
    const id = (msg?.tool_result as { id?: unknown } | undefined)?.id
    return typeof id === 'string' ? id : null
  }
  const now = find()
  if (now) return Promise.resolve(now)
  return new Promise(resolve => {
    const timer = setTimeout(() => { unsub(); resolve(null) }, ms)
    const unsub = useChatStore.subscribe(() => {
      const id = find()
      if (id) { clearTimeout(timer); unsub(); resolve(id) }
    })
  })
}

export default function MemoryCard({
  proposal, toolUseId, secondsLeft, blockReason, onApprove, onDeny,
}: {
  proposal: MemoryProposal
  toolUseId: string
  secondsLeft: number
  blockReason: string | null
  onApprove: () => Promise<boolean>
  onDeny: () => Promise<boolean>
}) {
  const [busy, setBusy] = useState(false)
  const run = async (fn: () => Promise<void>) => {
    if (busy) return
    setBusy(true)
    try { await fn() } finally { setBusy(false) }
  }

  const remember = () => run(async () => { await onApprove() })
  const editFirst = () => run(async () => {
    if (!(await onApprove())) return
    const id = await savedItemId(toolUseId)
    useMemoryStore.getState().openMemory({ editId: id })
  })
  const notNow = () => run(async () => {
    if (!(await onDeny())) return
    const item: NewMemoryItem = {
      kind: proposal.kind as NewMemoryItem['kind'],
      body: proposal.body,
      concept_keys: proposal.concept_keys,
      project_kind: proposal.project_kind,
      summary: proposal.summary,
    }
    try {
      await memoryApi.dismiss(item)
    } catch (err) {
      toast.error(`Not saved. The assistant may offer this again: ${errorWords(err)}`)
    }
  })

  return (
    <div className="mb-2" data-testid="memory-card">
      <div className="flex items-center gap-1.5 mb-1">
        <span className="px-1.5 py-0.5 rounded text-[9.5px] font-semibold uppercase tracking-wide border bg-accent-50 text-accent border-accent-100"
          data-testid="memory-card-kind">
          {KIND_WORDS[proposal.kind]}
        </span>
        <span className="text-[11px] text-muted">From this chat</span>
      </div>
      <div id="chat-confirmation-title" className="text-sm font-medium text-text mb-1"
        data-testid="memory-card-summary">
        {proposal.summary}
      </div>
      <div className="text-[11px] text-muted mb-1" data-testid="memory-card-scope">
        Just me · {appliesToWords(proposal.project_kind)}
      </div>
      {proposal.concept_keys.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-2">
          {proposal.concept_keys.map(k => (
            <span key={k} title={k}
              className="px-1 rounded bg-panel border border-border text-[9.5px] text-muted">
              {conceptWords(k)}
            </span>
          ))}
        </div>
      )}
      {blockReason && (
        <div className="mb-2 text-[11px] text-warn" data-testid="chat-confirm-mismatch">{blockReason}</div>
      )}
      <div className="flex items-center gap-2">
        <button
          className="px-2 py-1 text-xs rounded bg-amber-500/20 hover:bg-amber-500/30 border border-amber-500/50 disabled:opacity-40 disabled:cursor-not-allowed"
          onClick={remember} disabled={busy || !!blockReason} title={blockReason ?? undefined}
          data-testid="memory-card-remember">
          Remember
        </button>
        <button
          className="px-2 py-1 text-xs rounded bg-bg-2 hover:bg-bg-3 border border-border disabled:opacity-40 disabled:cursor-not-allowed"
          onClick={editFirst} disabled={busy || !!blockReason} title={blockReason ?? undefined}
          data-testid="memory-card-edit">
          Edit first
        </button>
        <button
          className="px-2 py-1 text-xs rounded bg-bg-2 hover:bg-bg-3 border border-border disabled:opacity-40"
          onClick={notNow} disabled={busy}
          data-testid="memory-card-not-now">
          Not now
        </button>
        <span className="font-mono text-[10px] text-muted ml-auto">{secondsLeft}s</span>
      </div>
    </div>
  )
}
