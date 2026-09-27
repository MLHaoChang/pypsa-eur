// Step 5 — Improve (guided-mode spec §5.3, §5.7): the review's high and
// medium findings, each with Why (the evidence), "Let the assistant do this"
// (its first action) and Ask; Check risks runs the FMEA sweep exactly as the
// FMEA tab does (the lifted hook), and a stress scenario is added through
// the assistant.
import { useState } from 'react'
import { ShieldAlert } from 'lucide-react'
import type { EhReviewFinding } from '../../../api/simulation'
import { useStartFmeaSweep } from '../../../hooks/useStartFmeaSweep'
import { useUIStore } from '../../../store/uiStore'
import { studyHasResults } from '../flow'
import { useHubDesignStore } from '../hubDesignStore'
import {
  ARCHETYPE_SHORT, actionText, askText, stressScenarioText,
} from '../delegate'
import { AskButton, CardShell, DelegateButton } from '../shared/CardShell'
import { Term } from '../shared/Term'
import { useHubReview, useHubStudy, useHubTemplate } from '../useHubData'

export function openFmeaTab(): void {
  const ui = useUIStore.getState()
  ui.setSlidePanel('results')
  ui.requestResultsTab('fmea')
}

function Finding({ f }: { f: EhReviewFinding }) {
  const [why, setWhy] = useState(false)
  const doText = actionText(f)
  return (
    <li data-testid={`hub-improve-${f.id}`}
      className="flex flex-col gap-2 rounded border border-border p-3 text-[12px]">
      <div className="flex items-start gap-2">
        <span className={`mt-0.5 text-[10px] font-semibold uppercase ${f.severity === 'high' ? 'text-danger' : 'text-warn'}`}>
          {f.severity === 'high' ? 'Important' : 'Worth doing'}
        </span>
        <span className="font-semibold text-text">{f.title}</span>
      </div>
      <p className="text-muted">{f.recommendation}</p>
      {doText && f.actions[0]?.effect && (
        <p className="text-muted">What it would do: {f.actions[0].effect}</p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" data-testid={`hub-improve-why-${f.id}`} aria-expanded={why}
          onClick={() => setWhy(w => !w)}
          className="px-2 py-1 text-[11px] text-muted hover:text-accent">
          {why ? '▾' : '▸'} Why
        </button>
        {doText && <DelegateButton testId={`hub-improve-do-${f.id}`} text={doText} />}
        <AskButton testId={`hub-improve-ask-${f.id}`} text={askText(f)} label="Ask" />
      </div>
      {why && (
        <div data-testid={`hub-improve-evidence-${f.id}`} className="rounded bg-bg p-2">
          <span className="text-[10px] uppercase tracking-wide text-muted">Technical evidence</span>
          <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 font-mono text-[11px]">
            {Object.entries(f.evidence ?? {}).map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-muted">{k}</dt>
                <dd className="text-text break-all">{typeof v === 'string' ? v : JSON.stringify(v)}</dd>
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
          {findings.map(f => <Finding key={f.id} f={f} />)}
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
          <DelegateButton testId="hub-improve-add-stress" text={stressScenarioText(context)}
            label="Add a stress scenario" />
        </div>
        {sweep.isSuccess && (
          <p data-testid="hub-improve-fmea-started" className="text-muted">
            Started — the check runs in the background; the risk table fills in as it goes.
          </p>
        )}
        <span className="text-muted"><Term k="stress_scenario">About stress scenarios</Term></span>
      </div>
    </CardShell>
  )
}
