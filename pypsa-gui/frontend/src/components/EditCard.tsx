/**
 * The edit card (LC-04, owner Q3): the old → new table every chat edit shows
 * before it applies, and the receipt after it was read back. The backend
 * contract is `harness/edit_card.py`; any edit kind that registers an adapter
 * there arrives in this one shape, so this component is the only renderer.
 */

type Scalar = string | number | boolean | null

export interface EditCardRow {
  component_class: string
  name: string
  field: string
  label: string
  old: Scalar
  old_known?: boolean
  new: Scalar
  unit: string
  role: 'typed' | 'derived' | 'follows'
  display?: { old: number | null; new: number; unit: string }
  readback?: Scalar
}

export interface EditCard {
  version: 1
  kind: string
  tool: string
  rows: EditCardRow[]
  says: string
  complete: boolean
  digest: string
}

export interface EditReceipt {
  version: 1
  digest: string
  outcome: 'verified' | 'uncertain'
  rows: EditCardRow[]
  says: string
  results_stale: boolean
  undo: { tool: string; available: boolean }
}

function object(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}

function rowsOk(rows: unknown): rows is EditCardRow[] {
  return Array.isArray(rows) && rows.length > 0 && rows.every(r => {
    const o = object(r)
    return !!o && typeof o.field === 'string' && typeof o.unit === 'string' && typeof o.role === 'string'
  })
}

/** A well-formed card or null; a malformed one is never shown as complete. */
export function editCardOf(value: unknown): EditCard | null {
  const o = object(value)
  if (!o || o.version !== 1 || typeof o.says !== 'string' || typeof o.digest !== 'string' || !rowsOk(o.rows)) return null
  return o as unknown as EditCard
}

export function editReceiptOf(value: unknown): EditReceipt | null {
  const o = object(value)
  if (!o || o.version !== 1 || (o.outcome !== 'verified' && o.outcome !== 'uncertain') || !rowsOk(o.rows)) return null
  return o as unknown as EditReceipt
}

function show(value: Scalar | undefined, known = true): string {
  if (!known) return 'Not read'
  if (value === null || value === undefined) return 'Empty'
  return typeof value === 'number' ? String(Number(value.toPrecision(12))) : String(value)
}

export function EditCardTable({ card, guided }: { card: EditCard; guided: boolean }) {
  const asset = (r: EditCardRow) => [r.component_class, r.name].filter(Boolean).join(' ') || '—'
  return (
    <div className="mb-2 text-xs" data-testid="chat-edit-card">
      <div className="overflow-x-auto">
        <table className="w-full text-left border-collapse">
          <caption className="text-left font-semibold mb-1">Changes to apply</caption>
          <thead><tr>{['Asset', 'Field', 'Old', 'New', 'Unit'].map(h => (
            <th key={h} scope="col" className="p-1 border-b border-border">{h}</th>
          ))}</tr></thead>
          <tbody>{card.rows.map((r, i) => {
            const follows = r.role !== 'typed'
            return (
              <tr key={i} className={follows ? 'text-muted' : ''} data-testid={`chat-edit-row-${r.role}`}>
                <td className="p-1 border-b border-border break-words">{asset(r)}</td>
                <td className="p-1 border-b border-border break-words">
                  {guided ? r.label : r.field}
                  {follows && <span className="block text-[10px]">
                    {r.role === 'derived' ? 'Follows from the change, read-only' : 'Follows from the change'}
                  </span>}
                </td>
                <td className="p-1 border-b border-border break-words">{show(r.old, r.old_known !== false)}</td>
                <td className="p-1 border-b border-border break-words font-medium">{show(r.new)}</td>
                <td className="p-1 border-b border-border break-words">
                  {r.unit || 'No unit'}
                  {r.display && <span className="block text-[10px] text-muted">
                    = {show(r.display.old)} → {show(r.display.new)} {r.display.unit}
                  </span>}
                </td>
              </tr>
            )
          })}</tbody>
        </table>
      </div>
      {!card.complete && <p className="mt-1 text-muted" data-testid="chat-edit-card-partial">
        These are the requested values. The current values were not read for this kind of change.
      </p>}
    </div>
  )
}

export function EditReceiptLine({ receipt, disabled, onRequest }: {
  receipt: EditReceipt; disabled: boolean; onRequest: (text: string) => void
}) {
  const verified = receipt.outcome === 'verified'
  return (
    <div className="mt-1 rounded border border-border p-2 font-sans text-xs" data-testid="chat-edit-receipt">
      <div className={verified ? '' : 'text-warn'}>
        {verified ? `${receipt.says}. Read back from the project.`
          : `${receipt.says || 'The change'}: the project does not show the expected values. Check them before going on.`}
      </div>
      {receipt.results_stale && <div className="text-muted" data-testid="chat-edit-stale">
        Results are now stale: they were computed before this change.
      </div>}
      {receipt.undo.available && (
        <button type="button" disabled={disabled} className="mt-1 text-accent disabled:opacity-40"
          data-testid="chat-edit-undo" onClick={() => onRequest('Undo that change with undo_last.')}>
          Undo
        </button>
      )}
    </div>
  )
}
