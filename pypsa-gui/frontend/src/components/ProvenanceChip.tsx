// One field's provenance on an asset card (U3 F part 2; UX assessment §4.3:
// "library default · you changed it · changed in Expert", the same chip in
// both faces). Nothing recorded renders nothing: unknown is not "default"
// (ADR-0001).
import type { ProvenanceEntry } from '../api/assetSchema'

export const PROVENANCE_CHIP_LABELS = {
  default: 'Default',
  customised: 'Customised',
  measured: 'Measured',
  illustrative: 'illustrative',
} as const

/** The chip's hover text: where the value came from, as the store recorded it. */
export function provenanceTitle(e: ProvenanceEntry): string {
  const parts: string[] = []
  if (e.status === 'default') parts.push('From the defaults')
  else if (e.status === 'customised') parts.push('Changed from the default')
  else parts.push('A measured value')
  if (e.source) parts.push(`source: ${e.source}${e.source_year != null ? ` (${e.source_year})` : ''}`)
  if (e.currency_year != null) parts.push(`money of ${e.currency_year}`)
  if (e.illustrative) parts.push('illustrative, not site-specific')
  return parts.join(' · ')
}

export function ProvenanceChip({ entry, testId }: { entry: ProvenanceEntry | undefined; testId?: string }) {
  if (!entry) return null
  const tone = entry.status === 'default' ? 'border-border text-muted'
    : entry.status === 'customised' ? 'border-accent/60 text-accent' : 'border-success/60 text-success'
  return (
    <span data-testid={testId} data-status={entry.status} title={provenanceTitle(entry)}
      className={`ml-1 inline-flex items-center rounded border px-1 text-[8.5px] leading-[13px] ${tone}`}>
      {PROVENANCE_CHIP_LABELS[entry.status]}
      {entry.status === 'default' && entry.illustrative && <span className="ml-0.5 italic">· {PROVENANCE_CHIP_LABELS.illustrative}</span>}
    </span>
  )
}
