// Assistant-assurance 06 (A5.3, UX 31, GK G4): one "·"-joined meta line per chat
// card. A card lists only the fields its kind can have, in a fixed order. A
// field with no value is never shown as 0, "—" or blank: it is named after
// "Not stated:" at the end of the line. Limitations stay on a line of their own,
// rendered by each card.

/** [what the field is called when it is missing, its value]. */
export type MetaEntry = readonly [name: string, value: string | number | null | undefined]

function stated(value: MetaEntry[1]): value is string | number {
  return typeof value === 'number' ? Number.isFinite(value) : typeof value === 'string' && value.trim() !== ''
}

export function metaLine(entries: readonly MetaEntry[]): string {
  const shown = entries.filter(([, value]) => stated(value)).map(([, value]) => String(value))
  const missing = entries.filter(([, value]) => !stated(value)).map(([name]) => name)
  return [...shown, ...(missing.length ? [`Not stated: ${missing.join(', ')}`] : [])].join(' · ')
}

export function CardMeta({ entries, className = '' }: { entries: readonly MetaEntry[]; className?: string }) {
  return <p className={className} data-testid="card-meta">{metaLine(entries)}</p>
}
