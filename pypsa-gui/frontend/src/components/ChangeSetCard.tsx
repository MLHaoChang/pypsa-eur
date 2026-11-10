/**
 * LC-47: one change-set card. A model response that proposes several ordinary
 * writes raises ONE confirmation whose rows each carry a tick box
 * (`harness/change_set.py`); the ticked rows are applied under one approval
 * and each is read back. This file holds the card's rows (shown inside
 * `ConfirmationCard`) and the one receipt that follows the batch.
 */
import { EditCardTable, editCardHeader, editCardOf, type EditCard } from './EditCard'

export interface ChangeSetItem {
  tool_use_id: string
  tool: string
  edit_card: EditCard
  /** "rows" when the card's rows name their own source, else the backend's words. */
  source: string
  digest: string
}

export interface ChangeSet {
  version: 1
  items: ChangeSetItem[]
  digest: string
  requirements: { checked: number; says: string }
}

export type ChangeSetRowStatus = 'applied' | 'unchanged' | 'uncertain' | 'failed' | 'stale' | 'unticked'

export interface ChangeSetReceiptRow {
  tool_use_id: string
  tool: string
  status: ChangeSetRowStatus
  error_kind: string | null
  label: string | null
}

export interface ChangeSetReceipt {
  version: 1
  decision: string
  rows: ChangeSetReceiptRow[]
  ticked: number
  landed: number
  failed: number
  all_applied: boolean
}

function object(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}

/** A well-formed set or null; a malformed one is never approvable. */
export function changeSetOf(value: unknown): ChangeSet | null {
  const o = object(value)
  const requirements = object(o?.requirements)
  if (!o || o.version !== 1 || !Array.isArray(o.items) || o.items.length < 2
    || typeof requirements?.says !== 'string') return null
  const ids = new Set<string>()
  for (const raw of o.items) {
    const item = object(raw)
    if (!item || typeof item.tool_use_id !== 'string' || !item.tool_use_id || ids.has(item.tool_use_id)
      || typeof item.tool !== 'string' || typeof item.source !== 'string' || !editCardOf(item.edit_card)) return null
    ids.add(item.tool_use_id)
  }
  return o as unknown as ChangeSet
}

const STATUSES: ReadonlySet<string> = new Set(['applied', 'unchanged', 'uncertain', 'failed', 'stale', 'unticked'])

export function changeSetReceiptOf(value: unknown): ChangeSetReceipt | null {
  const o = object(value)
  if (!o || o.version !== 1 || typeof o.decision !== 'string' || !Array.isArray(o.rows)
    || typeof o.ticked !== 'number' || typeof o.landed !== 'number' || typeof o.failed !== 'number') return null
  for (const raw of o.rows) {
    const row = object(raw)
    if (!row || typeof row.tool_use_id !== 'string' || typeof row.status !== 'string'
      || !STATUSES.has(row.status)) return null
  }
  return o as unknown as ChangeSetReceipt
}

/** What a row changes, in the same words in Guided and Expert (UX C5). */
export function changeSetItemLabel(item: ChangeSetItem): string {
  return editCardHeader(item.edit_card) || item.edit_card.says
}

export const CHANGE_SET_NONE_TICKED = 'Tick at least one change to apply.'

export function changeSetApplyLabel(ticked: number): string {
  return ticked === 1 ? 'Apply 1 change' : `Apply ${ticked} changes`
}

/** The rows of the card: a tick box, the old and new values and the source of each change. */
export function ChangeSetRows({ set, ticked, onToggle, disabled = false }: {
  set: ChangeSet; ticked: ReadonlySet<string>; onToggle: (toolUseId: string) => void; disabled?: boolean
}) {
  const total = set.items.length
  return (
    <div className="mb-2 text-xs" data-testid="chat-change-set">
      <ul className="m-0 list-none p-0">
        {set.items.map((item, i) => {
          const id = `chat-change-set-tick-${item.tool_use_id}`
          return (
            <li key={item.tool_use_id} className="mb-2 border-b border-border pb-1" data-testid="chat-change-set-item"
              data-ticked={ticked.has(item.tool_use_id)}>
              <div className="mb-1 flex items-start gap-2">
                <input id={id} type="checkbox" className="mt-0.5" checked={ticked.has(item.tool_use_id)}
                  disabled={disabled} onChange={() => onToggle(item.tool_use_id)}
                  data-testid="chat-change-set-tick" />
                <label htmlFor={id} className="font-medium text-text">{changeSetItemLabel(item)}</label>
              </div>
              {/* UX C5: the panel's field labels in both modes. */}
              <EditCardTable card={item.edit_card} guided
                labels={{ header: '', caption: `Change ${i + 1} of ${total}` }} />
              {item.source !== 'rows' && (
                <div className="text-[11px] text-muted" data-testid="chat-change-set-source">
                  Source: {item.source}
                </div>
              )}
            </li>
          )
        })}
      </ul>
      <div role="status" className="text-[11px] text-text" data-testid="chat-change-set-count">
        {ticked.size} of {total} ticked
      </div>
      <div className="text-[11px] text-muted" data-testid="chat-change-set-requirements">
        {set.requirements.says}
      </div>
    </div>
  )
}

/** A settled set that applied nothing, by how it ended (UX C4). */
const NOTHING_CHANGED: Record<string, string> = {
  deny: 'Cancelled: nothing changed',
  expired: 'Timed out: nothing changed',
  aborted: 'Stopped: nothing changed',
  revoked: 'Not applied: the rules for confirming changed while the card was open. Nothing changed',
  unavailable: 'Not applied: the app could not record the approval. Nothing changed',
}

const STALE_REASON = 'the project changed after the card was shown'

function statusWords(row: ChangeSetReceiptRow, reason: string | undefined): string {
  switch (row.status) {
    case 'applied': return 'Applied'
    case 'unchanged': return 'Applied: the project already had this value'
    case 'uncertain': return 'Applied: not confirmed yet'
    case 'stale': return `Failed: ${STALE_REASON}`
    case 'failed': return `Failed: ${reason || 'the change was refused'}`
    case 'unticked': return 'Not applied (unticked)'
  }
}

/** "3 of 4 applied · 1 failed": what landed out of what the user ticked (UX C2). */
export function changeSetCountLine(receipt: ChangeSetReceipt): string {
  const unchanged = receipt.rows.filter(r => r.status === 'unchanged').length
  const uncertain = receipt.rows.filter(r => r.status === 'uncertain').length
  return [
    `${receipt.landed} of ${receipt.ticked} applied`,
    receipt.failed ? `${receipt.failed} failed` : null,
    uncertain ? `${uncertain} not confirmed yet` : null,
    unchanged ? `${unchanged} already had the value` : null,
  ].filter(Boolean).join(' · ')
}

/** The receipt as plain text, for the transcript's own copy of the line. */
export function changeSetReceiptText(receipt: ChangeSetReceipt): string {
  return receipt.decision === 'approve' ? changeSetCountLine(receipt)
    : NOTHING_CHANGED[receipt.decision] ?? NOTHING_CHANGED.unavailable
}

/** The set's one receipt: a count line, then each row's outcome in words, its reason on its row. */
export function ChangeSetReceiptCard({ receipt, reasons, labels }: {
  receipt: ChangeSetReceipt
  /** Each row in the card's own words, by tool_use_id; the backend's label otherwise. */
  labels?: Record<string, string>
  /** A failed row's reason in plain words, by tool_use_id. */
  reasons?: Record<string, string>
}) {
  if (receipt.decision !== 'approve') {
    return (
      <div role="status" className="font-sans text-xs text-text" data-testid="chat-change-set-settled">
        {changeSetReceiptText(receipt)}.
      </div>
    )
  }
  return (
    <div className="rounded border border-border p-2 font-sans text-xs" data-testid="chat-change-set-receipt">
      <div role="status" className="font-medium text-text" data-testid="chat-change-set-receipt-count">
        {changeSetCountLine(receipt)}
      </div>
      <ul className="m-0 mt-1 list-none p-0">
        {receipt.rows.map(row => (
          <li key={row.tool_use_id} className="flex flex-wrap gap-x-2 border-t border-border py-0.5"
            data-testid="chat-change-set-receipt-row" data-status={row.status}>
            <span className="text-text">{labels?.[row.tool_use_id] || row.label || 'Change'}</span>
            <span className={row.status === 'failed' || row.status === 'stale' || row.status === 'uncertain'
              ? 'text-warn' : 'text-muted'}>
              {statusWords(row, reasons?.[row.tool_use_id])}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}
