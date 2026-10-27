// Step 3 — Goal (guided-mode spec §5.3): how much shortfall per year is
// acceptable, how strict to be on energy and how many simulation runs
// (advanced), the price of undelivered energy (read-only, with an assistant
// fix). The Run is in section 9, Readiness and run (`shared/GoalStudyRun.tsx`):
// this card sets the goal. The goal is the project's one stored reliability
// goal (W3b-R): the editor is Expert's Solver settings one, and "Save goal"
// writes only the fields typed here.
import { useEffect, useState } from 'react'
import StressScenarioEditor from '../../results/StressScenarioEditor'
import { useHubDesignStore } from '../hubDesignStore'
import { useUIStore } from '../../../store/uiStore'
import { goalPatchFromEdits, useReliabilityGoal, type GoalEdits } from '../../../hooks/useReliabilityGoal'
import { ReliabilityGoalEditor } from '../../../components/reliabilityGoal/ReliabilityGoalEditor'
import { VOLL_TEXT } from '../delegate'
import { CardShell, DelegateButton } from '../shared/CardShell'
import { LIVE_STUDY_EDIT, useLiveStudyRunning } from '../../../hooks/useLiveStudyRunning'
import { Term } from '../shared/Term'
import { useGoalStudy } from '../useGoalStudy'
import { ScopeCards } from '../../../components/flowScope/ScopeCards'
import { useInScope } from '../../../components/flowScope/useFlowScope'
import { ScopeGate } from '../../../components/flowScope/ScopeGate'

export function GoalCard() {
  const { project, running, voll, vollMissing } = useGoalStudy()
  const liveStudy = useLiveStudyRunning()
  const { goal, save } = useReliabilityGoal()
  const [edits, setEdits] = useState<GoalEdits>({})
  const { patch, error: editError } = goalPatchFromEdits(edits)
  const edited = Object.keys(patch).length > 0
  const saveGoal = () => save.mutate(patch, { onSuccess: () => setEdits({}) })
  const [advanced, setAdvanced] = useState(false)
  // "Change in Scenarios & optimisation ›" from a Results study asks for the
  // goal (`openReliabilityGoal`): in Guided this card is its home.
  const blockRequest = useUIStore(s => s.solverBlockRequest)
  useEffect(() => {
    if (blockRequest === 'goal') useUIStore.getState().clearSolverBlockRequest()
  }, [blockRequest])
  // A link to the stress editor opens "More settings" once.
  const advancedRequested = useHubDesignStore(s => s.goalAdvancedRequested)
  useEffect(() => {
    if (!advancedRequested) return
    setAdvanced(true)
    useHubDesignStore.getState().setGoalAdvancedRequested(false)
  }, [advancedRequested])
  // Section 8's scope (W4a): with Reliability › goal & study off, the goal
  // hides and the study is not run. VOLL stays: the optimisation prices lost
  // load with it (modular-cards §2).
  const reliabilityOn = useInScope('reliability', 'goal_study')
  const loleText = edits.target_lole_h ?? (goal?.target_lole_h.source !== 'default'
    ? String(goal?.target_lole_h.value ?? '') : '')

  return (
    <CardShell step="goal" testId="hub-card-goal" title="Goal">
      <ScopeCards testId="hub-scope-cards" collapsed />
      {!reliabilityOn && (
        <ScopeGate card="reliability" option="goal_study" testId="hub-goal-out-of-scope">{null}</ScopeGate>
      )}
      {reliabilityOn && <>
      <ReliabilityGoalEditor mode="guided" goal={goal} edits={edits}
        onChange={setEdits} disabled={running || !goal}
        fields={advanced ? ['target_lole_h', 'ens_cap_permyriad', 'mc_draws'] : ['target_lole_h']} />
      {/* The field shows the goal as stored ("not set"), as Expert does; a
          typical value is only suggested here, never shown as a default. */}
      {loleText.trim() === '' && (
        <span className="text-[12px] text-muted" data-testid="hub-goal-empty-hint">
          No goal yet. Type how many hours per year without power you can accept (for example 3)
          to get a pass or fail answer.
        </span>
      )}

      <div className="flex flex-col gap-2">
        <button type="button" data-testid="hub-goal-advanced" aria-expanded={advanced}
          onClick={() => setAdvanced(a => !a)}
          className="self-start text-[11px] text-muted hover:text-accent">
          {advanced ? '▾' : '▸'} More settings
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" data-testid="hub-goal-save" onClick={saveGoal}
          disabled={!edited || editError !== null || save.isPending || running}
          className="rounded bg-accent px-3 py-1.5 text-[12px] font-semibold text-white disabled:opacity-50">
          {save.isPending ? 'Saving…' : 'Save goal'}
        </button>
        {!edited && !save.isSuccess && (
          <span data-testid="hub-goal-no-changes" className="text-[12px] text-muted">
            No changes to save.
          </span>
        )}
        {save.isSuccess && !edited && (
          <span data-testid="hub-goal-saved" role="status" className="text-[12px] text-muted">
            Saved.
          </span>
        )}
        {save.isError && (
          <span data-testid="hub-goal-save-error" className="text-[12px] text-warn">
            The goal was not saved. Try again.
          </span>
        )}
      </div>
      </>}

      {/* The same editor Solver settings mounts: one editor for the stress conditions. */}
      {advanced && (
        <div data-testid="hub-goal-stress" className="flex flex-col gap-1">
          <span className="text-[12px] font-semibold text-text">Stress conditions</span>
          {/* Reliability › Stress conditions & FMEA owns the editor (W4a). */}
          <ScopeGate card="reliability" option="stress" testId="hub-goal-stress-scope">
            <StressScenarioEditor project={project} />
          </ScopeGate>
        </div>
      )}

      {/* A set price is a "More settings" item; one that is missing blocks the run, so it stays in view
          (UX review L3). */}
      {(advanced || voll == null || vollMissing) && (
      <div data-testid="hub-goal-voll" className="flex flex-wrap items-center gap-2 text-[12px]">
        <span><Term k="voll_plain">Price of undelivered energy</Term>:</span>
        {voll == null ? <span className="text-muted">…</span>
          : vollMissing ? (
            <>
              <span className="text-warn">not set — the study needs a price above zero</span>
              <DelegateButton testId="hub-goal-voll-fix" text={VOLL_TEXT}
                label="Let the assistant set it"
                disabled={liveStudy} disabledTitle={LIVE_STUDY_EDIT} />
            </>
          ) : <span className="text-text">
            €{voll.toLocaleString('en-US')} per{' '}<Term k="mwh">MWh</Term>
          </span>}
      </div>
      )}

      <p data-testid="hub-goal-run-hint" className="text-[12px] text-muted">
        The study runs from Readiness and run, once the checks pass; the button below goes there.
      </p>
    </CardShell>
  )
}
