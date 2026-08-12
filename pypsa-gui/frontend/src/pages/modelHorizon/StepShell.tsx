// The guided-step chrome for Model Horizon: a numbered rail (real buttons —
// keyboard reachable, same interaction model as ProjectTabs' role="tab" strip
// and PageKit's Seg) plus a title for whichever step is current, plus a slot
// for step-specific "advanced" content collapsed behind a native <details>
// disclosure (the pattern already used by SolverSettings.tsx and
// results/Economics.tsx's methodology notes).
//
// This component owns ONLY the frame. It renders whatever `children` the
// caller hands it for the current step — Task 3 hands it the page's existing
// section JSX unchanged; Tasks 4/5 will hand it step-specific extracted
// components instead. `advanced` is unused as of Task 3 (no caller passes it
// yet) — it exists so Tasks 4/5 don't have to touch this file to use it.
import type { ReactNode } from 'react'
import type { HorizonStepId } from '../modelHorizonModel'

/** Rail label for each step id. The single source of truth for step wording —
 * both the rail here and HorizonSummary's clickable lines import it, so a
 * step is never named one thing in the rail and another in the summary. */
export const STEP_LABELS: Record<HorizonStepId, string> = {
  mode: 'Mode',
  years: 'Investment years',
  economics: 'Economics',
  window: 'Snapshot window',
  sampling: 'Representative weeks',
  weights: 'Snapshot weightings',
}

export interface StepShellProps {
  /** Steps visible in rail order — already filtered by visibleSteps(isMultiPeriod). */
  steps: HorizonStepId[]
  current: HorizonStepId
  onSelect: (step: HorizonStepId) => void
  /** Chrome heading shown above the step body. Deliberately NOT just
   * STEP_LABELS[current] — several existing sections carry their own <h3>
   * with that exact text (e.g. "Snapshot weightings"), and giving this
   * heading the identical accessible name would make every
   * `getByRole('heading', { name: … })` lookup in the page ambiguous. The
   * caller is expected to compose something that reads distinctly, e.g.
   * `Step 3 of 6 — Economics`. */
  title: ReactNode
  children: ReactNode
  /** Optional advanced content for the current step, rendered inside a
   * collapsed disclosure below the step body. */
  advanced?: ReactNode
}

export function StepShell({ steps, current, onSelect, title, children, advanced }: StepShellProps) {
  return (
    <div className="flex flex-col gap-3.5">
      <nav
        aria-label="Model horizon steps"
        className="flex flex-wrap items-center gap-1 rounded-[7px] bg-panel border border-border p-0.5 self-start"
      >
        {steps.map((step, i) => {
          const active = step === current
          return (
            <button
              key={step}
              type="button"
              aria-current={active ? 'step' : undefined}
              onClick={() => onSelect(step)}
              className={`flex items-center gap-1.5 px-2.5 h-[26px] rounded-[5px] text-[11px] font-medium transition-colors
                ${active ? 'bg-bg text-text shadow-[0_1px_0_rgba(10,14,20,0.04)]' : 'text-muted hover:text-text'}`}
            >
              <span className={`font-mono text-[10px] ${active ? 'text-accent' : 'text-muted'}`}>{i + 1}</span>
              {STEP_LABELS[step]}
            </button>
          )
        })}
      </nav>

      <section>
        <h2 className="text-[12.5px] font-semibold text-text tracking-[-0.005em] mb-2.5">{title}</h2>
        {children}
        {advanced && (
          <details className="mt-3 border border-border rounded text-[11px]">
            <summary className="px-3 py-2 cursor-pointer text-muted hover:text-text select-none">Advanced</summary>
            <div className="px-3 py-2">{advanced}</div>
          </details>
        )}
      </section>
    </div>
  )
}
