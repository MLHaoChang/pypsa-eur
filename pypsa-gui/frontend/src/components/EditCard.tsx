/**
 * The edit card (LC-04, owner Q3): the old → new table every chat edit shows
 * before it applies, and the receipt after it was read back. The backend
 * contract is `harness/edit_card.py`; any edit kind that registers an adapter
 * there arrives in this one shape, so this component is the only renderer.
 */
import { FLOW_STATE_WORDS, type FlowSectionState, type FlowTarget } from '../api/flow'
import { useUIStore, type SlidePanel } from '../store/uiStore'
import { componentLabel } from '../utils/componentLabels'

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
  role: 'typed' | 'derived' | 'follows' | 'at_solve' | 'note'
  note?: string
  display?: { old: number | null; new: number; unit: string }
  readback?: Scalar
  /** Where the field lives in the project flow (api/flow.ts FlowTarget). */
  target?: FlowTarget
  /** Where the value came from: a table cell (apply_equipment_mapping), or a PDF page
   *  and the raw quote the server read it from (apply_document_values). */
  source?: { filename?: string; sheet?: string; cell?: string; page?: number; quote?: string
    raw: Scalar; raw_unit: string; formula?: boolean }
  /** A cost's stated money basis. */
  money?: { currency: string; money_year: number }
}

export interface EditCard {
  version: 1
  kind: string
  tool: string
  rows: EditCardRow[]
  says: string
  complete: boolean
  digest: string
  /** The flow section the edit lands in ("Connection & grid"). */
  section?: { id: string; label: string }
}

export interface EditReceipt {
  version: 1
  digest: string
  outcome: 'verified' | 'uncertain'
  rows: EditCardRow[]
  says: string
  results_stale: boolean
  undo: { tool: string; available: boolean }
  /** The section's flow state after the write; `changed` against the card. */
  section?: { id: string; label: string; state: string; changed: boolean }
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
  if (value === null || value === undefined) return 'not set'
  // The backend sends a non-finite number as its string ("inf"): an unbounded value.
  if (value === 'inf' || value === '-inf' || value === Infinity || value === -Infinity) return 'no limit'
  return typeof value === 'number' ? String(Number(value.toPrecision(12))) : String(value)
}

/** A quote as the card shows it: at most QUOTE_SHOWN characters, the rest in the tooltip. */
const QUOTE_SHOWN = 60
function clip(quote: string): string {
  return quote.length > QUOTE_SHOWN ? `${quote.slice(0, QUOTE_SHOWN - 1).trimEnd()}\u2026` : quote
}

/** A value in its display unit, rounded for reading (the stored value stays in the small line). */
function showDisplay(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'not set'
  return value.toLocaleString(undefined, { maximumFractionDigits: Math.abs(value) >= 100 ? 2 : 4 })
}

/** "Generator 'pv'": the class in the user's words and the component's name. */
function assetWords(componentClass: string, name: string): string {
  // "Settings" is a bucket, not a thing: the unit or item is named as it is ("BESS_NEW")
  if (componentClass === 'Settings') return name
  return componentClass ? `${componentLabel(componentClass)} '${name}'` : name
}

/** "Connection & grid › Line 'L2'" (the edit's flow section), or "Line 'L2'" when the
 * project has no flow; a bulk edit lists its names: "Connection & grid › Line 'L1', Line 'L2'". */
export function editCardHeader(card: EditCard): string {
  const byKey = new Map<string, string[]>()
  for (const r of card.rows) {
    if (r.role !== 'typed' || !r.component_class) continue
    const key = card.section && r.target?.section === card.section.id ? card.section.label : ''
    const names = byKey.get(key) ?? []
    const words = assetWords(r.component_class, r.name)
    if (!names.includes(words)) names.push(words)
    byKey.set(key, names)
  }
  return [...byKey].map(([key, names]) => key ? `${key} \u203a ${names.join(', ')}` : names.join(', ')).join('; ')
}

/** Show the field where it lives, without leaving the chat: the component's own
 * Properties (`panel` null) or the section's panel. */
function showInProperties(target: FlowTarget) {
  const ui = useUIStore.getState()
  if (target.panel) {
    ui.setSlidePanel(target.panel as SlidePanel)
    return
  }
  if (!target.component || !target.entity) return
  ui.setSelectedComponent({ type: target.component, name: target.entity })
  ui.openRightPanel()
  ui.setHighlightedComponent({ type: target.component, name: target.entity })
}

export function EditCardTable({ card, guided }: { card: EditCard; guided: boolean }) {
  const asset = (r: EditCardRow) => (r.name ? assetWords(r.component_class, r.name) : r.component_class) || '—'
  const header = editCardHeader(card)
  return (
    <div className="mb-2 text-xs" data-testid="chat-edit-card">
      {header && <div className="font-medium mb-1" data-testid="chat-edit-header">{header}</div>}
      <div className="overflow-x-auto">
        <table className="w-full text-left border-collapse">
          <caption className="text-left font-semibold mb-1">Changes to apply</caption>
          <thead><tr>{['Asset', 'Field', 'Old', 'New', 'Unit'].map(h => (
            <th key={h} scope="col" className="p-1 border-b border-border">{h}</th>
          ))}</tr></thead>
          <tbody>{card.rows.map((r, i) => {
            if (r.role === 'note') {
              return (
                <tr key={i} className="text-muted" data-testid="chat-edit-row-note">
                  <td className="p-1 border-b border-border break-words">{asset(r)}</td>
                  <td colSpan={4} className="p-1 border-b border-border break-words text-[11px]">{r.note}</td>
                </tr>
              )
            }
            const follows = r.role !== 'typed'
            const exists = r.field === 'exists'
            const value = (v: Scalar | undefined, known = true) => exists ? (v ? 'present' : 'absent') : show(v, known)
            return (
              <tr key={i} className={follows ? 'text-muted' : ''} data-testid={`chat-edit-row-${r.role}`}>
                <td className="p-1 border-b border-border break-words">{asset(r)}</td>
                <td className="p-1 border-b border-border break-words">
                  {exists ? (r.new ? 'Created by this change' : 'Removed by this change') : guided ? r.label : r.field}
                  {r.role === 'typed' && r.target && (r.target.panel || r.target.component) && (
                    <button type="button" className="block text-[10px] text-accent underline"
                      data-testid="chat-edit-show" onClick={() => showInProperties(r.target!)}>
                      {r.target.panel ? 'Show in its panel' : 'Show in Properties'}
                    </button>
                  )}
                  {r.source && <span className="block text-[10px] text-muted" data-testid="chat-edit-source"
                    title={r.source.quote}>
                    {(r.source.page != null
                      ? [r.source.filename, r.source.quote
                          ? `p. ${r.source.page} \u00b7 \u201c${clip(r.source.quote)}\u201d` : `p. ${r.source.page}`]
                      : [r.source.filename, r.source.sheet === '(csv)' ? null : r.source.sheet, r.source.cell])
                      .filter(Boolean).join(' \u203a ')}{' '}
                    ({[show(r.source.raw), r.source.raw_unit].filter(Boolean).join(' ')}{r.source.formula ? ', saved formula value' : ''})
                  </span>}
                  {r.money && <span className="block text-[10px] text-muted" data-testid="chat-edit-money">
                    {r.money.currency}, {r.money.money_year} money, not adjusted for inflation
                  </span>}
                  {follows && <span className="block text-[10px]">
                    {r.role === 'derived' ? 'Follows from the change, read-only'
                      : r.role === 'at_solve' ? (r.note ?? 'Set at solve time') : 'Follows from the change'}
                  </span>}
                </td>
                <td className="p-1 border-b border-border break-words">
                  {r.display && r.old_known !== false && typeof r.old === 'number' ? showDisplay(r.display.old) : value(r.old, r.old_known !== false)}</td>
                <td className="p-1 border-b border-border break-words font-medium">
                  {r.display ? showDisplay(r.display.new) : value(r.new)}</td>
                <td className="p-1 border-b border-border break-words">
                  {exists ? '' : (r.display?.unit || r.unit || 'No unit')}
                  {r.display && r.display.unit !== r.unit && <span className="block text-[10px] text-muted" data-testid="chat-edit-display">
                    stored as {show(r.new)} {r.unit}
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
      {receipt.section?.changed && <div data-testid="chat-edit-section">
        {receipt.section.label}: {FLOW_STATE_WORDS[receipt.section.state as FlowSectionState] ?? receipt.section.state}.
      </div>}
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
