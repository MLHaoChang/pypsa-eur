// How a result that is not a plain number says so (UX overhaul S2,
// docs/superpowers/plans/2026-10-14-ux-overhaul.md §2 "Results that haven't
// run"). A result is never hidden and never shown as 0 or "—" unless a zero
// was actually computed: it names its state, says why in one sentence, and
// offers the action that resolves it.
//
//   ready          — a value (rendered by the caller; no pill)
//   zero           — a computed zero; the caller shows 0 and what makes it zero
//   not_run        — the analysis that produces it has not run
//   running        — it is running now
//   not_modelled   — the last run did not model it (losses off, no VOLL…)
//   not_applicable — the network has nothing it could apply to (no lines…)
//   stale          — inputs changed since the run; the old value is dimmed
//   unavailable    — the run's payload cannot tell which of the above holds
//
// When every tile in a group shares one non-value state (losses not modelled,
// no lines), render one <ResultState> for the group instead of repeating the
// pill, reason and action on each tile. Kpi's own `state` is for a tile whose
// neighbours have values.
import type { ReactNode } from 'react'

export type ResultStateKind =
  | 'ready' | 'zero' | 'not_run' | 'running' | 'not_modelled' | 'not_applicable' | 'stale' | 'unavailable'

export type NonValueState = Exclude<ResultStateKind, 'ready' | 'zero'>

export interface ResultAction {
  label: string
  onClick: () => void
}

export const STATE_PILL: Record<NonValueState, { label: string; tone: string }> = {
  not_run: { label: 'Not run yet', tone: 'text-info bg-info/12' },
  running: { label: 'Running', tone: 'text-info bg-info/12' },
  not_modelled: { label: 'Not modelled', tone: 'text-warn bg-warn/12' },
  not_applicable: { label: 'Not applicable', tone: 'text-muted bg-muted/12' },
  stale: { label: 'Out of date', tone: 'text-warn bg-warn/12' },
  unavailable: { label: 'Not available for this run', tone: 'text-muted bg-muted/12' },
}

export function StatePill({ state }: { state: NonValueState }) {
  const pill = STATE_PILL[state]
  return (
    <span className={`self-start whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold ${pill.tone}`}>
      {pill.label}
    </span>
  )
}

export function StateAction({ action }: { action: ResultAction }) {
  return (
    <button
      type="button"
      onClick={action.onClick}
      className="self-start rounded-[6px] border border-border-2 bg-bg px-2 py-1 text-[11px] font-semibold text-text hover:border-border-3"
    >
      {action.label}
    </button>
  )
}

/** A whole section or chart in a non-value state. */
export default function ResultState({
  state, reason, action, children,
}: {
  state: NonValueState
  reason: string
  action?: ResultAction
  children?: ReactNode
}) {
  return (
    <div data-state={state} className="flex flex-col gap-1.5 rounded-[8px] border border-border bg-bg-2 px-3 py-2.5">
      <StatePill state={state} />
      <p className="text-[12px] leading-snug text-muted">{reason}</p>
      {children}
      {action && <StateAction action={action} />}
    </div>
  )
}
