/**
 * The edit card (LC-04, owner Q3): the old → new table every chat edit shows
 * before it applies, and the receipt after it was read back. The backend
 * contract is `harness/edit_card.py`; any edit kind that registers an adapter
 * there arrives in this one shape, so this component is the only renderer.
 */
import { FLOW_STATE_WORDS, type FlowSectionState, type FlowTarget } from '../api/flow'
import { useUIStore, type SlidePanel } from '../store/uiStore'
import { componentLabel } from '../utils/componentLabels'
import { carrierDisplayName } from '../pages/results/carrierAliases'

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
  display?: { old: number | null; new: number | null; unit: string }
  /** A setting's values in the Settings panel's words, which Guided shows ("Rolling horizon"). */
  words?: { old: Scalar; new: Scalar }
  /** False when the old value did not apply under the old settings (a rolling horizon
   *  while the strategy was full): the old cell shows "—". */
  old_applies?: boolean
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

/** A set the server resolved for one change (LC-16 OJ-22): what it selects, how many, by what factor. */
export interface EditCardSet {
  component_class: string
  attribute: string
  /** null when the change names its members (a names-form scale). */
  selector: { carrier?: string; bus?: string; all?: true } | null
  names: string[]
  size: number
  inactive: number
  /** The inactive members, marked in the table. */
  inactive_names?: string[]
  /** The backend's words, in model ids ("all Generator with carrier solar, as of this preview"). */
  words: string
  factor: number | null
  /** "× 1.2 (+20 %)", the same on the card, in its says and on the receipt. */
  factor_words: string | null
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
  /** One line per selector or scale change, shown above its rows. */
  sets?: EditCardSet[]
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
  /** The card's set lines, so Guided words the receipt as the card did. */
  sets?: EditCardSet[]
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

function setsOk(sets: unknown): boolean {
  return sets === undefined || (Array.isArray(sets) && sets.every(g => {
    const o = object(g)
    return !!o && typeof o.component_class === 'string' && typeof o.attribute === 'string'
      && typeof o.size === 'number' && Array.isArray(o.names) && typeof o.words === 'string'
  }))
}

/** A well-formed card or null; a malformed one is never shown as complete. */
export function editCardOf(value: unknown): EditCard | null {
  const o = object(value)
  if (!o || o.version !== 1 || typeof o.says !== 'string' || typeof o.digest !== 'string' || !rowsOk(o.rows)
    || !setsOk(o.sets)) return null
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

/** A card cell's number without a display value: six significant digits, never fewer than its whole
 * part, no grouping ("2030" stays a year); "no limit", "not set" and "Not read" stay `show`'s. */
function cellValue(value: Scalar | undefined, known = true): string {
  if (typeof value !== 'number' || !known || !Number.isFinite(value)) return show(value, known)
  return String(Number(value.toPrecision(Math.max(6, Math.trunc(Math.abs(value)).toString().length))))
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

/** The rows a set line stands for, by class, name and field. */
function setMembers(card: EditCard): Set<string> {
  return new Set((card.sets ?? []).flatMap(g => g.names.map(n => `${g.component_class}\u0000${n}\u0000${g.attribute}`)))
}

function inSet(members: Set<string>, r: EditCardRow): boolean {
  return members.has(`${r.component_class}\u0000${r.name}\u0000${r.field}`)
}

const MULTI_PORT = new Set(['Line', 'Link', 'Transformer'])

/** What a set selects, in the table's words: "Every Generator with carrier Solar at bus B1".
 * A names-form scale lists its members. */
function setPhrase(g: EditCardSet): string {
  if (!g.selector) return g.names.map(n => assetWords(g.component_class, n)).join(', ')
  const parts = [`Every ${componentLabel(g.component_class)}`]
  if (g.selector.carrier !== undefined) parts.push(`with carrier ${carrierDisplayName(g.selector.carrier)}`)
  // Lines, Transformers and Links have bus0, bus1…: the server matches any of them, as Expert's words say.
  if (g.selector.bus !== undefined) parts.push(`at bus ${g.selector.bus}${MULTI_PORT.has(g.component_class) ? ' (any port)' : ''}`)
  return parts.join(' ')
}

/** "Every Generator with carrier Solar · 3 assets · Capacity × 1.2 (+20 %)"; Expert keeps the backend's
 * ids. `label` is the field in words, `value` the new value ("→ 0.5") for a set given a value. */
export function setLine(g: EditCardSet, guided: boolean, label?: string, value = ''): string {
  const count = `${g.size} asset${g.size === 1 ? '' : 's'}${g.inactive ? `, ${g.inactive} inactive` : ''}`
  const field = guided && label ? label : g.attribute
  return `${guided ? setPhrase(g) : g.words} \u00b7 ${count} \u00b7 ${field} ${g.factor_words ?? value}`.trimEnd()
}

function cardSetLine(card: EditCard, g: EditCardSet, guided: boolean): string {
  const row = card.rows.find(r => r.role === 'typed' && r.component_class === g.component_class
    && r.field === g.attribute && g.names.includes(r.name))
  const unit = row ? row.display?.unit || row.unit : ''
  const value = row ? `\u2192 ${row.display ? showDisplay(row.display.new) : cellValue(row.new)}${unit ? ` ${unit}` : ''}` : ''
  return setLine(g, guided, row?.label, value)
}

/** "Connection & grid › Line 'L2'" (the edit's flow section), or "Line 'L2'" when the
 * project has no flow; a bulk edit lists its names: "Connection & grid › Line 'L1', Line 'L2'";
 * a set shows its phrase ("Every Generator with carrier Solar"), never its members' names. */
export function editCardHeader(card: EditCard): string {
  const byKey = new Map<string, string[]>()
  const members = setMembers(card)
  for (const r of card.rows) {
    if (r.role !== 'typed' || !r.component_class) continue
    const key = card.section && r.target?.section === card.section.id ? card.section.label : ''
    const names = byKey.get(key) ?? []
    const group = inSet(members, r) ? card.sets!.find(g => g.component_class === r.component_class
      && g.attribute === r.field && g.names.includes(r.name)) : undefined
    const words = group ? setPhrase(group) : assetWords(r.component_class, r.name)
    if (!names.includes(words)) names.push(words)
    byKey.set(key, names)
  }
  return [...byKey].map(([key, names]) => key ? `${key} \u203a ${names.join(', ')}` : names.join(', ')).join('; ')
}

/** A row's old and new cells. Guided shows a setting's value in the panel's words; an old
 * value that did not apply shows "—". */
function rowValues(r: EditCardRow, guided: boolean, value = cellValue): [string, string] {
  const known = r.old_known !== false
  const words = guided ? r.words : undefined
  const old = r.old_applies === false ? '\u2014'
    : words ? (words.old == null ? show(r.old) : String(words.old))
    : r.display && known && typeof r.old === 'number' ? showDisplay(r.display.old) : value(r.old, known)
  const now = words ? (words.new == null ? show(r.new) : String(words.new))
    : r.display ? showDisplay(r.display.new) : value(r.new)
  return [old, now]
}

/** The Guided headline: each typed change in the table's words and display unit
 * ("Line 'L1', Capacity 300 → 500 MVA"), never the raw field or the stored unit. */
export const HEADLINE_CAP = 4

export function editCardWords(card: EditCard): string {
  const members = setMembers(card)
  const lines = [...(card.sets ?? []).map(g => cardSetLine(card, g, true)),
    ...card.rows.filter(r => r.role === 'typed' && !inSet(members, r)).map(r => {
    const asset = assetWords(r.component_class, r.name)
    if (r.field === 'exists') return `${r.new ? 'Adds' : 'Removes'} ${asset}`
    const [old, now] = rowValues(r, true)
    const unit = r.display?.unit || r.unit
    return `${asset}, ${r.label} ${old} \u2192 ${now}${unit ? ` ${unit}` : ''}`
  })]
  // A long card keeps its headline short; the table below lists every row. One hidden line
  // is not worth "and 1 more", so it shows.
  return lines.length <= HEADLINE_CAP + 1 ? lines.join('; ')
    : `${lines.slice(0, HEADLINE_CAP).join('; ')}; and ${lines.length - HEADLINE_CAP} more changes`
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

/** Another card's words for the same table (the LC-17 sweep card: a case name, Base and Case). */
export interface EditTableLabels { header?: string; caption?: string; old?: string; new?: string; noUnit?: string }

export function EditCardTable({ card, guided, labels }: { card: EditCard; guided: boolean; labels?: EditTableLabels }) {
  const inactive = new Set((card.sets ?? []).flatMap(g => (g.inactive_names ?? []).map(n => `${g.component_class}\u0000${n}`)))
  const asset = (r: EditCardRow) => {
    const words = (r.name ? assetWords(r.component_class, r.name) : r.component_class) || '—'
    return inactive.has(`${r.component_class}\u0000${r.name}`) ? `${words} (inactive)` : words
  }
  const header = labels?.header ?? editCardHeader(card)
  return (
    <div className="mb-2 text-xs" data-testid="chat-edit-card">
      {header && <div className="font-medium mb-1" data-testid="chat-edit-header">{header}</div>}
      {(card.sets ?? []).map((g, i) => (
        <div key={i} className="mb-1" data-testid="chat-edit-set">{cardSetLine(card, g, guided)}</div>
      ))}
      <div className="overflow-x-auto">
        <table className="w-full text-left border-collapse">
          <caption className="text-left font-semibold mb-1">{labels?.caption ?? 'Changes to apply'}</caption>
          <thead><tr>{['Asset', 'Field', labels?.old ?? 'Old', labels?.new ?? 'New', 'Unit'].map(h => (
            <th key={h} scope="col" className="p-1 border-b border-border">{h}</th>
          ))}</tr></thead>
          <tbody>{card.rows.map((r, i) => {
            if (r.role === 'note' && !r.component_class && !r.name) return null
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
            const value = (v: Scalar | undefined, known = true) => exists ? (v ? 'present' : 'absent') : cellValue(v, known)
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
                <td className="p-1 border-b border-border break-words">{rowValues(r, guided, value)[0]}</td>
                <td className="p-1 border-b border-border break-words font-medium">{rowValues(r, guided, value)[1]}</td>
                <td className="p-1 border-b border-border break-words">
                  {exists ? '' : (r.display?.unit || r.unit || (labels?.noUnit ?? 'No unit'))}
                  {r.display && r.display.unit !== r.unit && typeof r.new === 'number' && <span className="block text-[10px] text-muted" data-testid="chat-edit-display">
                    stored as {show(r.new)} {r.unit}
                  </span>}
                </td>
              </tr>
            )
          })}</tbody>
        </table>
      </div>
      {card.rows.filter(r => r.role === 'note' && !r.component_class && !r.name).map((r, i) => (
        <p key={i} className="mt-1 text-muted" data-testid="chat-edit-card-note">{r.note}</p>
      ))}
      {!card.complete && <p className="mt-1 text-muted" data-testid="chat-edit-card-partial">
        These are the requested values. The current values were not read for this kind of change.
      </p>}
    </div>
  )
}

export function EditReceiptLine({ receipt, disabled, onRequest, guided = false }: {
  receipt: EditReceipt; disabled: boolean; onRequest: (text: string) => void
  /** Guided words the receipt as the card's headline did, never in ids. */
  guided?: boolean
}) {
  const verified = receipt.outcome === 'verified'
  const says = guided ? editCardWords({ rows: receipt.rows, sets: receipt.sets } as EditCard) || receipt.says : receipt.says
  return (
    <div className="mt-1 rounded border border-border p-2 font-sans text-xs" data-testid="chat-edit-receipt">
      <div className={verified ? '' : 'text-warn'}>
        {verified ? `${says}. Read back from the project.`
          : `${says || 'The change'}: the project does not show the expected values. Check them before going on.`}
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
