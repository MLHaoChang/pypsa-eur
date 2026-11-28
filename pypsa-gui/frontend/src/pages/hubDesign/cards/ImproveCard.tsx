// Step 5 — Improve (guided-mode spec §5.3, §5.7): the review's high and
// medium findings, each with Why (the evidence), "Open in Expert" (the same
// change by hand, filled in; template-e2e 22, IMPROVE-MANUAL-1), "Let the
// assistant do this" (its first action) and Ask; Check risks runs the FMEA sweep exactly as the
// FMEA tab does (the lifted hook), and a stress scenario is added through
// the assistant.
import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ExternalLink, ShieldAlert } from 'lucide-react'
import { resultsApi, type EhReviewFinding } from '../../../api/simulation'
import { fmeaModesRefetchInterval, useStartFmeaSweep } from '../../../hooks/useStartFmeaSweep'
import { useStudyFinishedInvalidation } from '../../../hooks/useStudyFinishedInvalidation'
import { LIVE_STUDY_EDIT, useLiveStudyRunning } from '../../../hooks/useLiveStudyRunning'
import { useUIStore } from '../../../store/uiStore'
import { nk } from '../../../utils/queryKeys'
import { studyHasResults } from '../flow'
import { useReliabilityGoal } from '../../../hooks/useReliabilityGoal'
import {
  ARCHETYPE_SHORT, actionLabel, actionTexts, askText, stressScenarioText,
} from '../delegate'
import { evidenceValue, feederAdvice, plainWords } from '../plainWords'
import { AskButton, CardShell, DelegateButton } from '../shared/CardShell'
import { Term } from '../shared/Term'
import { formatElapsed, useElapsed } from '../../../hooks/useElapsed'
import { useHubLevers, useHubReport, useHubReview, useHubStudy, useHubTemplate } from '../useHubData'
import { DEST_WHERE, improvePlan, openImproveInExpert, openStressScenarios, type ImprovePlan } from '../improvePrefill'
import { cheaperDuration, leversFinding } from '../cheaperDuration'

export function openFmeaTab(): void {
  const ui = useUIStore.getState()
  ui.setSlidePanel('results')
  ui.requestResultsTab('fmea')
}

/** The secondary "Open in Expert" button: the same change by hand, with no
 *  assistant (KEY-LATER-1), so it is never disabled. */
function OpenInExpert({ f, plan }: { f: EhReviewFinding; plan: ImprovePlan }) {
  const where = `Open in Expert: ${DEST_WHERE[plan.dest]}`
  return (
    <button type="button" data-testid={`hub-improve-open-${f.id}`} title={where} aria-label={where}
      onClick={() => openImproveInExpert(plainWords(f.title), plan)}
      className="inline-flex items-center gap-1 px-2 py-1 border border-border rounded text-[11px] text-text hover:border-accent hover:text-accent">
      <ExternalLink size={12} /> Open in Expert
    </button>
  )
}

function Finding({ f, liveStudy }: { f: EhReviewFinding; liveStudy: boolean }) {
  const [why, setWhy] = useState(false)
  const { archetype } = useReliabilityGoal()
  const plan = improvePlan(f, archetype)
  // One message per action (§5.7); the queue sends them one after another.
  const doTexts = actionTexts(f)
  const more = doTexts.length - 1
  const doLabels = doTexts.map((_, i) => i === 0 ? actionLabel(f)
    : `${actionLabel(f)} (step ${i + 1} of ${doTexts.length})`)
  const effect = f.actions?.[0]?.effect
  // The only feeder to a must-stay-on bus: no re-run fixes it, so the card
  // says what does (asset-investment walkthrough 2026-11-17, issue 04).
  const advice = feederAdvice(f)
  // The card shows the title and the action in plain words; the review's own
  // prose (recommendation, effect) and its numbers go in "Why" (§5.8, gate B3).
  const technical: [string, string][] = [
    ['recommendation', f.recommendation],
    ...(effect ? [['action', effect] as [string, string]] : []),
    ...Object.entries(f.evidence ?? {}).map(([k, v]) => [k, evidenceValue(v)] as [string, string]),
  ]
  return (
    <li data-testid={`hub-improve-${f.id}`}
      className="flex flex-col gap-2 rounded border border-border p-3 text-[12px]">
      <div className="flex items-start gap-2">
        <span className={`mt-0.5 text-[10px] font-semibold uppercase ${f.severity === 'high' ? 'text-danger' : 'text-warn'}`}>
          {f.severity === 'high' ? 'Important' : 'Worth doing'}
        </span>
        <span className="font-semibold text-text">{plainWords(f.title)}</span>
      </div>
      {doTexts.length > 0 && effect && (
        <p className="text-muted">
          {/* P26 gate: the button opens a "Run the reliability study" card,
              so a study re-run says so; the review's effect is its aim. */}
          {f.actions?.[0]?.tool === 'run_eh_study'
            ? <>This change: run the reliability study again. Aim: {plainWords(effect)}</>
            : <>This change: {plainWords(effect)}</>}
          {more > 0 ? `, then ${more} more step${more === 1 ? '' : 's'}, one at a time` : ''}.
        </p>
      )}
      {advice && (
        <p data-testid={`hub-improve-advice-${f.id}`} className="text-muted">{advice}</p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" data-testid={`hub-improve-why-${f.id}`} aria-expanded={why}
          onClick={() => setWhy(w => !w)}
          className="px-2 py-1 text-[11px] text-muted hover:text-accent">
          {why ? '▾' : '▸'} Why
        </button>
        <OpenInExpert f={f} plan={plan} />
        {doTexts.length > 0 && (
          <DelegateButton testId={`hub-improve-do-${f.id}`} text={doTexts} display={doLabels}
            disabled={liveStudy} disabledTitle={LIVE_STUDY_EDIT} />
        )}
        <AskButton testId={`hub-improve-ask-${f.id}`} text={askText(f)} label="Ask" />
      </div>
      {plan.unshown.length > 0 && (
        <p data-testid={`hub-improve-unshown-${f.id}`} className="text-[11px] text-muted">
          Expert does not fill in {plan.unshown.join(', ')}: set {plan.unshown.length === 1 ? 'it' : 'them'} there by hand, or let the assistant do this.
        </p>
      )}
      {why && (
        <div data-testid={`hub-improve-evidence-${f.id}`} className="rounded bg-bg p-2">
          <span className="text-[10px] uppercase tracking-wide text-muted">Technical evidence</span>
          <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 font-mono text-[11px]">
            {technical.map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-muted">{k}</dt>
                <dd className="text-text break-words">{v}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    </li>
  )
}

export function ImproveCard() {
  const viewerReadOnly = useUIStore(s => s.readOnly && s.readOnlyReason === 'viewer')
  const { study, running } = useHubStudy()
  const { review } = useHubReview(studyHasResults(study))
  // The Verdict's cheaper battery duration, repeated as a row; its link stays
  // on the Results card (WALK-IMPROVE-1).
  const report = useHubReport(study, running)
  const levers = useHubLevers(!running && leversFinding(review?.findings) != null)
  const cheaper = cheaperDuration(review?.findings, levers, report)
  const { template } = useHubTemplate()
  const { archetype } = useReliabilityGoal()
  const sweep = useStartFmeaSweep()
  const liveStudy = useLiveStudyRunning()
  // P26: follow a sweep started here on FmeaTab's own query and options, so
  // /simulation/status is re-read when it ends (its closing re-solve leaves
  // fresh dispatch). FmeaTab does this only while it is open.
  const project = useUIStore(s => s.currentProject)
  // A5: the mutation's state is per mount, not per project — a sweep started
  // on project A left `isSuccess` (the "Started" line, the follow query)
  // standing after a switch to B. Remember the project the success belongs
  // to (so the very first render after a switch already stops following —
  // an effect alone would let one fetch of B's modes through) and reset the
  // mutation when the project changes.
  const successFor = useRef<string | null | undefined>(undefined)
  if (!sweep.isSuccess) successFor.current = undefined
  else if (successFor.current === undefined) successFor.current = project
  const following = sweep.isSuccess && successFor.current === project
  const { reset: resetSweep } = sweep
  const firstProject = useRef(project)
  useEffect(() => {
    if (firstProject.current === project) return
    firstProject.current = project
    resetSweep()
  }, [project, resetSweep])
  const { data: modes } = useQuery({
    queryKey: nk(project, 'results', 'fmea_modes'),
    queryFn: () => resultsApi.getFmeaModes(),
    // One extra read after the sweep leaves `running` (A5): the first
    // non-running sample can still carry the partial rows.
    refetchInterval: fmeaModesRefetchInterval,
    enabled: following,
  })
  useStudyFinishedInvalidation(!following || modes === undefined ? undefined
    : (modes as { sweep_status?: string | null } | null)?.sweep_status ?? null)
  const sweepRunning = following
    && ((modes as { sweep_status?: string | null } | null | undefined)?.sweep_status ?? 'running') === 'running'
  const sweepElapsed = formatElapsed(useElapsed(sweepRunning))
  const findings = (review?.findings ?? [])
    .filter(f => f.severity === 'high' || f.severity === 'medium')
  const context = `a ${ARCHETYPE_SHORT[archetype]} site`
    + (template ? ` like the ${template.name} example` : '')

  return (
    <CardShell step="improve" testId="hub-card-improve" title="Improve">
      {review && findings.length === 0 ? (
        <p data-testid="hub-improve-none" className="text-[12px] text-muted">
          {cheaper
            ? 'Nothing urgent. One cheaper option:'
            : 'Nothing urgent: the study found no important or worthwhile change.'}
        </p>
      ) : (
        <ul data-testid="hub-improve-list" className="flex flex-col gap-2">
          {findings.map(f => <Finding key={f.id} f={f} liveStudy={liveStudy} />)}
        </ul>
      )}
      {cheaper && (
        <p data-testid="hub-improve-cheaper-duration"
          className="rounded border border-border p-3 text-[12px] text-text">
          {cheaper}
        </p>
      )}

      <div className="flex flex-col gap-2 border-t border-border/60 pt-3 text-[12px]">
        <span className="font-semibold text-text">
          <Term k="fmea_check">Check what happens when equipment fails</Term>
        </span>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" data-testid="hub-improve-fmea"
            onClick={() => sweep.mutate()} disabled={sweep.isPending || viewerReadOnly}
            className="inline-flex items-center gap-1.5 rounded bg-accent px-3 py-1.5 text-[12px] font-semibold text-white disabled:opacity-50">
            <ShieldAlert size={12} /> {sweep.isPending ? 'Starting…' : 'Check risks (FMEA)'}
          </button>
          <button type="button" data-testid="hub-improve-open-fmea" onClick={openFmeaTab}
            className="rounded border border-border px-2 py-1 text-[11px] text-muted hover:border-accent hover:text-accent">
            See the risk table
          </button>
        </div>
        {following && (
          <p data-testid="hub-improve-fmea-started" className="text-muted">
            Started: the check runs in the background; the risk table fills in as it goes.
            {sweepRunning && ` Running for ${sweepElapsed}.`}
          </p>
        )}
        <div data-testid="hub-improve-stress-row" className="flex flex-wrap items-center gap-2">
          <span className="text-text"><Term k="stress_scenario">Hard conditions to test</Term>:</span>
          <button type="button" data-testid="hub-improve-open-stress" onClick={openStressScenarios}
            title="Open: Goal › More settings › Hard conditions" aria-label="Open the hard conditions: Goal › More settings"
            className="inline-flex items-center gap-1 px-2 py-1 border border-border rounded text-[11px] text-text hover:border-accent hover:text-accent">
            <ExternalLink size={12} /> Open the hard conditions
          </button>
          <DelegateButton testId="hub-improve-add-stress" text={stressScenarioText(context)}
            label="Add a stress scenario" />
        </div>
      </div>
    </CardShell>
  )
}
