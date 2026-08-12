// The guided-step chrome for Model Horizon: a numbered rail (real buttons —
// keyboard reachable, same "real <button>, not a clickable div" interaction
// model as ProjectTabs' tab strip and PageKit's Seg — though this is a
// stepper, not a tablist, so it marks the current entry with
// `aria-current="step"` rather than ProjectTabs' `role="tab"`/`aria-selected`)
// plus a title for whichever step is current, plus a slot
// for step-specific "advanced" content collapsed behind a native <details>
// disclosure (the pattern already used by SolverSettings.tsx and
// results/Economics.tsx's methodology notes).
//
// This component owns ONLY the frame. It renders whatever `children` the
// caller hands it for the current step — Task 3 hands it the page's existing
// section JSX unchanged; Tasks 4/5 will hand it step-specific extracted
// components instead. `advanced` is unused as of Task 3 (no caller passes it
// yet) — it exists so Tasks 4/5 don't have to touch this file to use it.
//
// Task 4 note: the disclosure is a CONTROLLED <details> (`open`/`onToggle`
// wired to local state), not a bare uncontrolled one, and `advanced` is only
// spliced into the tree while that state is true. A plain uncontrolled
// <details> only hides its content visually/from the accessibility tree in a
// real layout engine — jsdom has none, so `advanced`'s content (e.g. an
// 8,760-row table) would sit in the DOM, and be findable by
// `getByRole`, even while collapsed. The advanced slot exists specifically
// to keep large content OUT of the tree until asked for, so that has to be
// true under test as well as in a browser. Local state also resets to
// closed on every step change, so a step never inherits another step's
// disclosure state.
import { useEffect, useState, type ReactNode } from 'react'
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
  const [advancedOpen, setAdvancedOpen] = useState(false)
  // A step switch (e.g. clicking a rail entry) reuses this same StepShell
  // instance — it never unmounts between steps — so without this, opening
  // Advanced on one step would leave it open when the user navigates to a
  // different step that also has advanced content.
  useEffect(() => {
    setAdvancedOpen(false)
  }, [current])

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
          <details
            className="mt-3 border border-border rounded text-[11px]"
            open={advancedOpen}
            onToggle={e => setAdvancedOpen((e.target as HTMLDetailsElement).open)}
          >
            <summary className="px-3 py-2 cursor-pointer text-muted hover:text-text select-none">Advanced</summary>
            {advancedOpen && <div className="px-3 py-2">{advanced}</div>}
          </details>
        )}
      </section>
    </div>
  )
}
