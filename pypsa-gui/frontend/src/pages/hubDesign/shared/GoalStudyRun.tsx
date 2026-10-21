// The Guided Run (design §3.3: one Run): in section 9, Readiness → run. It
// starts the reliability study with the goal set on the Goal card, through the
// same `buildEhStudyBody` the Expert panel uses. Disabled exactly when the
// flow's verdict (`useRunGate('all')`) or the study's own inputs say it would
// be refused; the reason is shown, never a silent grey button.
//
// Running / failed / aborted are read from the study record (P24-BE gate
// N2), and the running line is this component's own text — never the review
// route's chat-oriented message (N3).
import { Loader2, Play, Square } from 'lucide-react'
import { useRunGate } from '../../../hooks/useRunGate'
import { formatElapsed, useElapsed } from '../../../hooks/useElapsed'
import { AddToScope } from '../../../components/flowScope/ScopeCards'
import { useInScope } from '../../../components/flowScope/useFlowScope'
import { useGoalStudy } from '../useGoalStudy'

export const RELIABILITY_OUT_OF_SCOPE =
  'Outside scope: add Reliability to set a goal and run the study.'

export function GoalStudyRun() {
  const gate = useRunGate('all')
  const {
    project, study, running, templateUnknown, refetchTemplate, vollMissing, built, run, abort, problem,
  } = useGoalStudy()
  // The scope (W4a): with Reliability › goal & study off, the study is not run.
  const reliabilityOn = useInScope('reliability', 'goal_study')
  const status = study?.status
  const again = status === 'failed' || status === 'aborted' || status === 'done'
  const pipeline = study?.report?.pipeline
  // Plain words, and a count only when the record has one — never "0 of N".
  const budget = pipeline?.budget_solves ?? study?.budget_solves
  const elapsed = formatElapsed(useElapsed(running))
  const runningText = (pipeline?.budget_solves && typeof pipeline.solves_consumed === 'number'
    ? `Studying… ${pipeline.solves_consumed} of ${pipeline.budget_solves} calculation steps`
    : budget ? `Studying… (up to ${budget} calculation steps)` : 'Studying…')
    + ` · ${elapsed} elapsed`

  return (
    <div data-testid="hub-run" className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" data-testid="hub-run-start"
          onClick={() => run.mutate()}
          disabled={running || run.isPending || built.error !== null || vollMissing || !project
            || templateUnknown || gate.blocked || !reliabilityOn}
          title={templateUnknown ? 'The template could not be read — retry below.'
            : !reliabilityOn ? RELIABILITY_OUT_OF_SCOPE : undefined}
          className="inline-flex items-center gap-1.5 rounded bg-accent px-3 py-1.5 text-[12px] font-semibold text-white disabled:opacity-50">
          {running ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
          {running ? 'Studying…' : again ? 'Run again' : 'Run study'}
        </button>
        {running && (
          <>
            <span data-testid="hub-run-running" className="text-[12px] text-muted">{runningText}</span>
            <button type="button" data-testid="hub-run-abort" onClick={() => abort.mutate()}
              title="Stops at the next stage boundary; the network is left as it was."
              className="inline-flex items-center gap-1 rounded border border-border px-2 py-1 text-[11px] text-muted hover:border-danger hover:text-danger">
              <Square size={10} /> Abort
            </button>
          </>
        )}
      </div>
      {!reliabilityOn && (
        <AddToScope card="reliability" option="goal_study" testId="hub-run-out-of-scope" />
      )}
      {templateUnknown && (
        <p data-testid="hub-run-template-error" className="flex flex-wrap items-center gap-2 text-[12px] text-warn">
          This project's recommended settings could not be read, so the study cannot start
          with them yet.
          <button type="button" data-testid="hub-run-template-retry"
            onClick={() => void refetchTemplate()}
            className="rounded border border-warn/60 px-2 py-0.5 text-[11px] hover:bg-warn/10">
            Retry
          </button>
        </p>
      )}
      {problem && <p data-testid="hub-run-blocked" className="text-[12px] text-warn">{problem}</p>}
      {!running && status === 'failed' && (
        <p data-testid="hub-run-error" className="text-[12px] text-danger">
          The last study failed: {study?.error ?? 'no reason was given'}
        </p>
      )}
      {!running && status === 'aborted' && (
        <p data-testid="hub-run-error" className="text-[12px] text-warn">
          Stopped before the end — the results cover only the parts that finished.
        </p>
      )}
    </div>
  )
}
