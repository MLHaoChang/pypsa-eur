// Completeness chips (the EH panel's house pattern), shared by the Energy Hub
// reference-design panel and the Investment tab (IC P3 WP3.5): one chip per
// report section, "name: status", toned by status, with an optional note as
// its title.

export type SectionStatus = 'ok' | 'not_established' | 'skipped' | (string & {})

export function statusTone(status: SectionStatus): string {
  // The theme's success token: the accent is the brand red, which read as an
  // error on every "ok" chip (EH click-through obstacle 5).
  if (status === 'ok') return 'text-success'
  if (status === 'skipped') return 'text-muted'
  return 'text-warn'
}

export interface CompletenessRow { name: string; status: SectionStatus; note?: string | null }

export function CompletenessChips({ rows, testId, itemTestIdPrefix, label }: {
  rows: CompletenessRow[]
  testId: string
  itemTestIdPrefix: string
  /** Accessible name of the list (screen readers announce it). */
  label?: string
}) {
  if (rows.length === 0) return null
  return (
    <ul className="flex flex-wrap gap-1.5" data-testid={testId} aria-label={label}>
      {rows.map(({ name, status, note }) => (
        <li
          key={name}
          className={`text-[10px] border border-border rounded px-1.5 py-0.5 ${statusTone(status)}`}
          data-testid={`${itemTestIdPrefix}${name}`}
          data-status={status}
          title={note ?? undefined}
        >
          {name}: {status}
        </li>
      ))}
    </ul>
  )
}
