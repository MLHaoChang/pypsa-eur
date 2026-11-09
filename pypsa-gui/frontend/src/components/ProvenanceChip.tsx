// One field's provenance on an asset card (U3 F part 2; UX assessment §4.3:
// "library default · you changed it · changed in Expert", the same chip in
// both faces). Nothing recorded renders nothing: unknown is not "default"
// (ADR-0001).
import type { ProvenanceEntry } from '../api/assetSchema'
import { FIELD_LABELS } from '../utils/fieldLabels'

export const PROVENANCE_CHIP_LABELS = {
  default: 'Default',
  customised: 'Customised',
  measured: 'Measured',
  illustrative: 'illustrative',
  reference: 'Reference',
} as const

/** A value the user applied, after a preview, from a Library cost reference. */
function applied(e: ProvenanceEntry): boolean {
  return e.status === 'customised' && !!e.approved_by
}

/** The chip's hover text: where the value came from, as the store recorded it. */
export function provenanceTitle(e: ProvenanceEntry): string {
  const parts: string[] = []
  if (e.status === 'default') parts.push('From the defaults')
  else if (applied(e)) parts.push('Applied from a Library cost reference')
  else if (e.status === 'customised') parts.push('Changed from the default')
  else parts.push('A measured value')
  // An applied reference's source is an id (`eu-dea-2026-08#…`); say whose catalogue it is.
  const source = applied(e) && e.source?.startsWith('eu-dea-')
    ? 'the Danish Energy Agency technology catalogue' : e.source
  if (source) parts.push(`source: ${source}${e.source_year != null ? ` (${e.source_year})` : ''}`)
  if (e.currency_year != null) parts.push(`money of ${e.currency_year}`)
  if (applied(e)) parts.push(`applied by ${e.approved_by}${e.approved_at ? ` on ${e.approved_at.slice(0, 10)}` : ''}`)
  if (e.illustrative) parts.push('illustrative, not site-specific')
  return parts.join(' · ')
}

const STALE_LABEL = 'out of date'

function num(v: number): string {
  return v.toLocaleString(undefined, { maximumFractionDigits: 4 })
}

const labelOf = (key: string | null): string | null => (key ? FIELD_LABELS[key] ?? key.replace(/_/g, ' ') : null)

/** Where a person re-prices: the edit form has the compare control beside the cost; the read-out and the table do not. */
export type StaleWhere = 'card' | 'readout' | 'table' | 'ledger'

const REPRICE: Record<Exclude<StaleWhere, 'ledger'>, string> = {
  card: 'Compare with references to re-price.',
  readout: 'Edit the asset, then compare with references to re-price.',
  table: 'Open the asset and compare with references to re-price.',
}

/** The ledger row is not the asset, so its hint names the asset to open (UX, provenance-graph 02). */
const hintFor = (where: StaleWhere, asset?: string) =>
  where === 'ledger' ? `Open ${asset ?? 'the asset'} in Expert and compare with references to re-price.` : REPRICE[where]

/**
 * The visible line for a cost priced through a scale input that no longer matches (TCU-05; reasons are
 * never hover-only). Form: "Out of date: <reason>. Priced with <Field> <recorded>, now <current>."
 * Backend reasons are matched, not echoed, so the words stay ours (no keys, no repr quotes).
 */
export function staleNote(e: Pick<ProvenanceEntry, 'stale'> | undefined, where: StaleWhere, asset?: string): string | null {
  const body = staleBody(e, where, asset)
  return body === null ? null : `Out of date: ${body}`
}

/** `staleNote` without its "Out of date: " lead, for a line that names the asset first. */
export function staleBody(e: Pick<ProvenanceEntry, 'stale'> | undefined, where: StaleWhere, asset?: string): string | null {
  const s = e?.stale
  if (!s) return null
  const hint = hintFor(where, asset)
  const field = labelOf(s.field)
  const now = labelOf(s.current_field)
  if (s.recorded == null || !field) {
    const detail = /\(([^)]*)\)/.exec(s.reason)?.[1]
    const why = s.reason.includes('malformed') ? 'its pricing record is unreadable'
      : `the input it was priced with was not recorded${detail ? ` (${detail})` : ''}`
    return `${why}. ${hint}`
  }
  const priced = `Priced with ${field} ${num(s.recorded)}`
  if (s.current == null || !now) {
    return `${field} is no longer available. ${priced}. ${hint}`
  }
  const changed = now === field ? `${field} changed` : `the price now reads ${now}`
  return `${changed}. ${priced}, now ${now === field ? '' : `${now} `}${num(s.current)}. ${hint}`
}

/** The "out of date" chip; its key is the note line next to it (`staleNote`). */
export function StaleChip({ entry, testId }: { entry: ProvenanceEntry | undefined; testId?: string }) {
  if (!entry?.stale) return null
  return (
    <span data-testid={testId} data-stale="true" title={staleNote(entry, 'card') ?? STALE_LABEL}
      className="ml-1 inline-flex items-center whitespace-nowrap rounded border border-warn/60 px-1 text-[8.5px] leading-[13px] text-warn">
      {STALE_LABEL}
    </span>
  )
}

export function ProvenanceChip({ entry, testId }: { entry: ProvenanceEntry | undefined; testId?: string }) {
  if (!entry) return null
  const tone = entry.status === 'default' ? 'border-border text-muted'
    : entry.status === 'customised' ? 'border-accent/60 text-accent' : 'border-success/60 text-success'
  return (
    <>
      <span data-testid={testId} data-status={entry.status} title={provenanceTitle(entry)}
        className={`ml-1 inline-flex items-center rounded border px-1 text-[8.5px] leading-[13px] ${tone}`}>
        {applied(entry) ? PROVENANCE_CHIP_LABELS.reference : PROVENANCE_CHIP_LABELS[entry.status]}
        {entry.status === 'default' && entry.illustrative && <span className="ml-0.5 italic">· {PROVENANCE_CHIP_LABELS.illustrative}</span>}
      </span>
      <StaleChip entry={entry} testId={testId ? `${testId}-stale` : undefined} />
    </>
  )
}
