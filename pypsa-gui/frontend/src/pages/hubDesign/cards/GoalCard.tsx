// Step 3 — Goal (guided-mode spec §5.3): how much shortfall per year is
// acceptable, how strict to be on energy and how many simulation runs
// (advanced), the price of undelivered energy (read-only, with an assistant
// fix). The Run is in section 9, Readiness and run (`shared/GoalStudyRun.tsx`):
// this card sets the goal. The goal is the project's one stored reliability
// goal (W3b-R): the editor is Expert's Solver settings one, and "Save goal"
// writes only the fields typed here.
import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { simulationApi } from '../../../api/simulation'
import { nk } from '../../../utils/queryKeys'
import StressScenarioEditor from '../../results/StressScenarioEditor'
import { useHubDesignStore } from '../hubDesignStore'
import { useUIStore } from '../../../store/uiStore'
import { assumedLole, CO2_LIMIT, goalPatchFromEdits, useReliabilityGoal, type GoalEdits } from '../../../hooks/useReliabilityGoal'
import { Co2LimitField, ReliabilityGoalEditor } from '../../../components/reliabilityGoal/ReliabilityGoalEditor'
import { VOLL_TEXT } from '../delegate'
import { GOAL_VOLL_LABEL } from './goalCardWords'
import { CardShell, DelegateButton } from '../shared/CardShell'
import { LIVE_STUDY_EDIT, useLiveStudyRunning } from '../../../hooks/useLiveStudyRunning'
import { Term } from '../shared/Term'
import { useGoalStudy } from '../useGoalStudy'
import { ScopeCards } from '../../../components/flowScope/ScopeCards'
import { useInScope } from '../../../components/flowScope/useFlowScope'
import { ScopeGate } from '../../../components/flowScope/ScopeGate'

export function GoalCard() {
  const viewerReadOnly = useUIStore(s => s.readOnly && s.readOnlyReason === 'viewer')
  const { project, running, voll, vollMissing } = useGoalStudy()
  const liveStudy = useLiveStudyRunning()
  const { goal, save } = useReliabilityGoal()
  const [edits, setEdits] = useState<GoalEdits>({})
  // Section 8's scope (W4a): with Reliability › goal & study off, the goal
  // hides and the study is not run. "Save goal" then saves the CO₂ limit only:
  // a hidden field is never sent, and its error never blocks the save (UX 29).
  const reliabilityOn = useInScope('reliability', 'goal_study')
  const saved: GoalEdits = reliabilityOn ? edits : (CO2_LIMIT in edits ? { [CO2_LIMIT]: edits[CO2_LIMIT] } : {})
  const { patch, error: editError } = goalPatchFromEdits(saved)
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
  // VOLL stays out of scope too: the optimisation prices lost load with it (modular-cards §2).
  // The CO₂ limit (DC review 02) is saved with the goal but is not a
  // reliability setting: it stays when the reliability goal is out of scope.
  const { data: cfg } = useQuery({
    queryKey: nk(project, 'solverConfig'), queryFn: simulationApi.getSolverConfig, enabled: !!project,
  })
  const co2Limit = cfg ? (cfg.co2_limit_t_per_yr ?? null) : undefined
  // "No goal yet" only when there is none at all: no typed, saved or template
  // value and no site-type assumed goal (template-e2e 06).
  const loleText = edits.target_lole_h ?? (goal?.target_lole_h.source !== 'default'
    ? String(goal?.target_lole_h.value ?? '') : String(assumedLole(goal) ?? ''))

  return (
    <CardShell step="goal" testId="hub-card-goal" title="Goal">
      <ScopeCards testId="hub-scope-cards" collapsed />
      {!reliabilityOn && (
        <ScopeGate card="reliability" option="goal_study" testId="hub-goal-out-of-scope">{null}</ScopeGate>
      )}
      {reliabilityOn && <ReliabilityGoalEditor mode="guided" goal={goal} edits={edits}
        onChange={setEdits} disabled={running || !goal || viewerReadOnly}
        fields={advanced ? ['target_lole_h', 'ens_cap_permyriad', 'mc_draws'] : ['target_lole_h']} />}
      {/* With no goal and no assumed one the field reads "not set", as Expert
          does; a typical value is only suggested here, never shown as a default. */}
      {reliabilityOn && loleText.trim() === '' && (
        <span className="text-[12px] text-muted" data-testid="hub-goal-empty-hint">
          No goal yet. Type how many hours per year without power you can accept (for example 3)
          to get a pass or fail answer.
        </span>
      )}

      {reliabilityOn && <div className="flex flex-col gap-2">
        <button type="button" data-testid="hub-goal-advanced" aria-expanded={advanced}
          onClick={() => setAdvanced(a => !a)}
          className="self-start text-[11px] text-muted hover:text-accent">
          {advanced ? '▾' : '▸'} More settings
        </button>
      </div>}
      {co2Limit !== undefined && (
        <Co2LimitField mode="guided" co2Limit={co2Limit} edits={edits} onChange={setEdits} disabled={running || viewerReadOnly} />
      )}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" data-testid="hub-goal-save" onClick={saveGoal}
          disabled={!edited || editError !== null || save.isPending || running || viewerReadOnly}
          className="rounded bg-accent px-3 py-1.5 text-[12px] font-semibold text-white disabled:opacity-50">
          {save.isPending ? 'Saving…' : 'Save goal'}
        </button>
        {!edited && !editError && !save.isSuccess && (
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
        <span><Term k="voll_plain">{GOAL_VOLL_LABEL}</Term>:</span>
        {voll == null ? <span className="text-muted">…</span>
          : vollMissing ? (
            <>
              <span className="text-warn">not set: the study needs a price above zero</span>
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
