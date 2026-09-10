// The guided-step chrome for Model Horizon: a numbered rail (real buttons —
// keyboard reachable, same "real <button>, not a clickable div" interaction
// model as ProjectTabs' tab strip and PageKit's Seg — though this is a
// stepper, not a tablist, so it marks the current entry with
// `aria-current="step"` rather than ProjectTabs' `role="tab"`/`aria-selected`)
// plus a title for whichever step is current, plus a slot
// for step-specific "advanced" content behind a <details> disclosure.
//
// This component owns ONLY the frame. It renders whatever `children` the
// caller hands it for the current step — Task 3 hands it the page's existing
// section JSX unchanged; Tasks 4/5 will hand it step-specific extracted
// components instead.
//
// The disclosure is a CONTROLLED <details> (`open`/`onToggle` wired to local
// state) for every consumer, closed by default and reset to closed on every
// step change — a step must never inherit another step's disclosure state,
// and this same StepShell instance persists across step switches (it never
// unmounts between them).
//
// By DEFAULT (`unmountAdvancedWhenCollapsed` unset/false), `advanced`'s
// content stays mounted in the DOM the whole time — the exact pattern
// SolverSettings.tsx and results/Economics.tsx's methodology notes already
// use: a plain, always-mounted <details> that the browser hides natively
// while closed. That keeps the content reachable by in-page find (Chrome's
// find-in-page searches inside closed <details> and auto-expands on a
// match — something an unmounted subtree cannot support) and matches the
// existing convention exactly.
//
// `unmountAdvancedWhenCollapsed={true}` is opt-in for a consumer whose
// advanced content is large enough that always mounting it defeats the
// point of hiding it — Task 4's per-row snapshot-weightings table can be
// 8,760 rows on an hourly model, so StepWeights.tsx passes true and
// genuinely unmounts. Task 4's per-period window table (at most a handful
// of rows, one per investment period) has no such size argument and uses
// the default, staying mounted like every other <details> in this codebase.
//
// (Note for anyone reading jsdom-based tests against this file: a closed
// native <details> is NOT hidden from `getByRole` under jsdom — jsdom's
// default stylesheet has no rule for it, unlike a real layout engine. Tests
// against the default (mounted) mode must assert on the `open` attribute,
// not on DOM absence; DOM absence is only a valid assertion for the
// `unmountAdvancedWhenCollapsed={true}` case.)
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
  /** Opt-in: don't mount `advanced` into the DOM until the disclosure is
   * opened (and remove it again on close). Default false — see the file
   * header for when to reach for this instead of the native always-mounted
   * behaviour. */
  unmountAdvancedWhenCollapsed?: boolean
}

export function StepShell({
  steps, current, onSelect, title, children, advanced,
  unmountAdvancedWhenCollapsed = false,
}: StepShellProps) {
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
            {(!unmountAdvancedWhenCollapsed || advancedOpen) && (
              <div className="px-3 py-2">{advanced}</div>
            )}
          </details>
        )}
      </section>
    </div>
  )
}
