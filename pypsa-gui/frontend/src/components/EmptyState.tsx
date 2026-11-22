// One empty state for a tab or panel that has nothing to show yet (UI-COHESION-1, F6).
//
// Shape: a title line that names the state ("Not run yet", "Not yet available",
// "No project open"), one sentence that gives the reason or the next step as
// visible text (never a tooltip), and at most one action. Each tab keeps its own
// words; this component only makes the layout and the pattern one.
// `role="status"` so a state that appears after loading is announced.
import type { ReactNode } from 'react'

export interface EmptyStateAction {
  label: string
  onClick: () => void
}

export default function EmptyState({ title, children, action, testId, actionTestId }: {
  /** The state, in a few words: "Not run yet", "Not yet available". */
  title: string
  /** The reason or the next step, one sentence. */
  children?: ReactNode
  /** At most one action. */
  action?: EmptyStateAction
  testId?: string
  actionTestId?: string
}) {
  return (
    <div role="status" data-testid={testId} data-empty-state
      className="flex flex-col items-start gap-1.5 rounded-card border border-border bg-bg-2 px-3 py-2.5">
      <div className="text-h3 font-semibold text-text">{title}</div>
      {children && <p className="text-lead leading-snug text-muted">{children}</p>}
      {action && (
        <button type="button" onClick={action.onClick} data-testid={actionTestId}
          className="mt-0.5 rounded-control border border-border-2 bg-bg px-2 py-1 text-body font-semibold text-text hover:border-border-3">
          {action.label}
        </button>
      )}
    </div>
  )
}
