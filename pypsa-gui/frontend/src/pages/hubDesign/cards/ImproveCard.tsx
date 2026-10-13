// Step 5 — Improve (guided-mode spec §5.3, §5.7): the review's high and
// medium findings, each with Why (the evidence), "Let the assistant do this"
// (its first action) and Ask; Check risks runs the FMEA sweep exactly as the
// FMEA tab does (the lifted hook), and a stress scenario is added through
// the assistant.
import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ShieldAlert } from 'lucide-react'
import { resultsApi, type EhReviewFinding } from '../../../api/simulation'
import { fmeaModesRefetchInterval, useStartFmeaSweep } from '../../../hooks/useStartFmeaSweep'
import { useStudyFinishedInvalidation } from '../../../hooks/useStudyFinishedInvalidation'
import { LIVE_STUDY_EDIT, useLiveStudyRunning } from '../../../hooks/useLiveStudyRunning'
import { useUIStore } from '../../../store/uiStore'
import { nk } from '../../../utils/queryKeys'
import { studyHasResults } from '../flow'
import { useHubDesignStore } from '../hubDesignStore'
import {
  ARCHETYPE_SHORT, actionLabel, actionTexts, askText, stressScenarioText,
} from '../delegate'
import { evidenceValue, plainWords } from '../plainWords'
import { AskButton, CardShell, DelegateButton } from '../shared/CardShell'
import { Term } from '../shared/Term'
import { useHubReview, useHubStudy, useHubTemplate } from '../useHubData'

export function openFmeaTab(): void {
  const ui = useUIStore.getState()
  ui.setSlidePanel('results')
  ui.requestResultsTab('fmea')
}

function Finding({ f, liveStudy }: { f: EhReviewFinding; liveStudy: boolean }) {
  const [why, setWhy] = useState(false)
  // One message per action (§5.7); the queue sends them one after another.
  const doTexts = actionTexts(f)
  const more = doTexts.length - 1
  const doLabels = doTexts.map((_, i) => i === 0 ? actionLabel(f)
    : `${actionLabel(f)} (step ${i + 1} of ${doTexts.length})`)
  const effect = f.actions?.[0]?.effect
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
            ? <>The assistant would run the reliability study again. Aim: {plainWords(effect)}</>
            : <>The assistant would {plainWords(effect)}</>}
          {more > 0 ? `, then ${more} more step${more === 1 ? '' : 's'}, one at a time` : ''}.
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" data-testid={`hub-improve-why-${f.id}`} aria-expanded={why}
          onClick={() => setWhy(w => !w)}
          className="px-2 py-1 text-[11px] text-muted hover:text-accent">
          {why ? '▾' : '▸'} Why
        </button>
        {doTexts.length > 0 && (
          <DelegateButton testId={`hub-improve-do-${f.id}`} text={doTexts} display={doLabels}
            disabled={liveStudy} disabledTitle={LIVE_STUDY_EDIT} />
        )}
        <AskButton testId={`hub-improve-ask-${f.id}`} text={askText(f)} label="Ask" />
      </div>
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
  const { study } = useHubStudy()
  const { review } = useHubReview(studyHasResults(study))
  const { template } = useHubTemplate()
  const archetype = useHubDesignStore(s => s.archetype)
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
  const findings = (review?.findings ?? [])
    .filter(f => f.severity === 'high' || f.severity === 'medium')
  const context = `a ${ARCHETYPE_SHORT[archetype]} site`
    + (template ? ` like the ${template.name} example` : '')

  return (
    <CardShell step="improve" testId="hub-card-improve" title="Improve">
      {review && findings.length === 0 ? (
        <p data-testid="hub-improve-none" className="text-[12px] text-muted">
          Nothing urgent: the study found no important or worthwhile change.
        </p>
      ) : (
        <ul data-testid="hub-improve-list" className="flex flex-col gap-2">
          {findings.map(f => <Finding key={f.id} f={f} liveStudy={liveStudy} />)}
        </ul>
      )}

      <div className="flex flex-col gap-2 border-t border-border/60 pt-3 text-[12px]">
        <span className="font-semibold text-text">
          <Term k="fmea_check">Check what happens when equipment fails</Term>
        </span>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" data-testid="hub-improve-fmea"
            onClick={() => sweep.mutate()} disabled={sweep.isPending}
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
            Started — the check runs in the background; the risk table fills in as it goes.
          </p>
        )}
        <div data-testid="hub-improve-stress-row" className="flex flex-wrap items-center gap-2">
          <span className="text-text"><Term k="stress_scenario">Hard conditions to test</Term>:</span>
          <DelegateButton testId="hub-improve-add-stress" text={stressScenarioText(context)}
            label="Add a stress scenario" />
        </div>
      </div>
    </CardShell>
  )
}
