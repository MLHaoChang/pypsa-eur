// Step 3 — Goal (guided-mode spec §5.3): how much shortfall per year is
// acceptable, how strict to be on energy (advanced), the price of undelivered
// energy (read-only, with an assistant fix). The Run is in section 9,
// Readiness → run (`shared/GoalStudyRun.tsx`): this card sets the goal.
import { useEffect, useState } from 'react'
import StressScenarioEditor from '../../results/StressScenarioEditor'
import { useHubDesignStore } from '../hubDesignStore'
import { VOLL_TEXT } from '../delegate'
import { CardShell, DelegateButton } from '../shared/CardShell'
import { LIVE_STUDY_EDIT, useLiveStudyRunning } from '../../../hooks/useLiveStudyRunning'
import { Term } from '../shared/Term'
import { useGoalStudy } from '../useGoalStudy'
import { ScopeCards } from '../../../components/flowScope/ScopeCards'
import { useInScope } from '../../../components/flowScope/useFlowScope'
import { ScopeGate } from '../../../components/flowScope/ScopeGate'

export function GoalCard() {
  const { project, running, readiness, voll, vollMissing } = useGoalStudy()
  const liveStudy = useLiveStudyRunning()
  const loleTarget = useHubDesignStore(s => s.loleTarget)
  const ensCap = useHubDesignStore(s => s.ensCap)
  const setLoleTarget = useHubDesignStore(s => s.setLoleTarget)
  const setEnsCap = useHubDesignStore(s => s.setEnsCap)
  const [advanced, setAdvanced] = useState(false)
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
  const ensDefault = readiness?.pack_defaults?.ens_cap_permyriad

  return (
    <CardShell step="goal" testId="hub-card-goal" title="Goal">
      <ScopeCards testId="hub-scope-cards" collapsed />
      {!reliabilityOn && (
        <ScopeGate card="reliability" option="goal_study" testId="hub-goal-out-of-scope">{null}</ScopeGate>
      )}
      {reliabilityOn && <>
      <label className="flex flex-col gap-1 text-[12px]">
        <span className="font-semibold text-text">
          <Term k="shortfall_hours">Allowed shortfall (hours per year)</Term>
        </span>
        <input type="number" step="any" min={0} data-testid="hub-goal-lole"
          value={loleTarget} disabled={running}
          placeholder="e.g. 3"
          onChange={e => setLoleTarget(e.target.value)}
          className="w-32 rounded border border-border bg-bg px-2 py-1 font-mono text-[12px] text-text" />
        {loleTarget.trim() === '' && (
          <span className="text-muted" data-testid="hub-goal-empty-hint">
            No goal yet. Type how many hours per year without power you can accept (for example 3)
            to get a pass or fail answer.
          </span>
        )}
      </label>

      <div className="flex flex-col gap-2">
        <button type="button" data-testid="hub-goal-advanced" aria-expanded={advanced}
          onClick={() => setAdvanced(a => !a)}
          className="self-start text-[11px] text-muted hover:text-accent">
          {advanced ? '▾' : '▸'} More settings
        </button>
        {advanced && (
          <label className="flex flex-col gap-1 text-[12px]">
            <span className="font-semibold text-text">
              <Term k="energy_strictness">Energy strictness (parts per 10 000 of yearly energy)</Term>
            </span>
            <input type="number" step="any" min={0} data-testid="hub-goal-ens"
              value={ensCap} disabled={running}
              placeholder={ensDefault != null ? `${ensDefault} (default)` : 'default'}
              onChange={e => setEnsCap(e.target.value)}
              className="w-32 rounded border border-border bg-bg px-2 py-1 font-mono text-[12px] text-text" />
          </label>
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
        The study runs from Readiness → run, once the checks pass; the button below goes there.
      </p>
    </CardShell>
  )
}
