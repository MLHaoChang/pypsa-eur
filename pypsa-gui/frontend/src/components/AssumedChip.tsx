// One input's standing as an assumption (WALK-IMPROVE-1 b): a value a template
// ships, with where it comes from, until the person types their own or keeps
// it. Shared by the finance inputs, the readiness checklist and the template
// and goal screens, in Guided and Expert alike. The source is words on the page, never
// hover-only (TCU-05): "Assumed <source>, <year>: <note>". The state is a word,
// never colour alone.

/** Which of the three an input is. Typed wins over confirmed: an edit replaces the assumption. */
export type AssumedState = 'assumed' | 'confirmed' | 'typed'

export interface AssumedChipProps {
  /** Where the shipped value comes from (the template's finance ledger names it). */
  source?: string | null
  /** A year, or an ISO date when the source is dated more finely. */
  sourceYear?: number | string | null
  /** Why the value stands, when the source alone does not say (e.g. "assumed, no primary source"). */
  note?: string | null
  /** The person typed their own value: the chip reads "Stated" and the source goes. */
  typed?: boolean
  /** The person kept the shipped value: the chip reads "Confirmed" and keeps its source. */
  confirmed?: boolean
  testId?: string
}

export const ASSUMED_CHIP_LABELS: Record<AssumedState, string> = {
  assumed: 'Assumed',
  confirmed: 'Confirmed',
  typed: 'Stated',
}

const TONE: Record<AssumedState, string> = {
  assumed: 'bg-warn/15 text-warn',
  confirmed: 'bg-success/10 text-success',
  typed: 'bg-accent/10 text-accent',
}

const CHIP = 'inline-flex items-center whitespace-nowrap rounded px-1'

export function assumedState({ typed, confirmed }: Pick<AssumedChipProps, 'typed' | 'confirmed'>): AssumedState {
  return typed ? 'typed' : confirmed ? 'confirmed' : 'assumed'
}

/** The visible words beside the chip: source and year, then the note. Typed values have none. */
export function assumedSourceText(p: AssumedChipProps): string | null {
  if (assumedState(p) === 'typed') return null
  const src = p.source?.trim()
  const head = src ? `${src}${p.sourceYear != null ? `, ${p.sourceYear}` : ''}` : 'source not recorded'
  const note = p.note?.trim()
  return note ? `${head}: ${note}` : head
}

export function AssumedChip(props: AssumedChipProps) {
  const state = assumedState(props)
  const words = assumedSourceText(props)
  return (
    <span data-testid={props.testId} data-state={state} className="inline-flex flex-wrap items-baseline gap-x-1">
      <span className={`${CHIP} ${TONE[state]}`}>{ASSUMED_CHIP_LABELS[state]}</span>
      {words && <span className="text-muted" data-testid={props.testId ? `${props.testId}-source` : undefined}>{words}</span>}
    </span>
  )
}

/** The key under a card's chips, one per card: it explains all three states. */
export function AssumedKey({ testId }: { testId?: string }) {
  return (
    <p className="text-muted" data-testid={testId}>
      <span className={`${CHIP} ${TONE.assumed}`}>{ASSUMED_CHIP_LABELS.assumed}</span> a default, its source
      beside it; editing it makes it stated ·{' '}
      <span className={`${CHIP} ${TONE.confirmed}`}>{ASSUMED_CHIP_LABELS.confirmed}</span> a default you kept ·{' '}
      <span className={`${CHIP} ${TONE.typed}`}>{ASSUMED_CHIP_LABELS.typed}</span> typed by you
    </p>
  )
}
