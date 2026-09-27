// Step 4 — Results (guided-mode spec §5.3, §5.5): the verdict in one
// sentence, the cost, the three costliest risks and what the study could not
// establish — from GET /results/eh_review and the report the Expert panel
// shows. "Open full report" goes to that panel.
import { FileText } from 'lucide-react'
import { useUIStore } from '../../../store/uiStore'
import { fmeaTopRows, notEstablishedNotes } from '../../results/EhReferenceDesignPanel'
import { fmtCurrency } from '../../results/shared'
import { studyHasResults } from '../flow'
import { headline } from '../headline'
import { CardShell } from '../shared/CardShell'
import { Term } from '../shared/Term'
import { useHubReport, useHubReview, useHubStudy } from '../useHubData'

/** Report sections in words — the card never shows a stage id (§5.8). */
export const SECTION_LABEL: Record<string, string> = {
  target: 'Reliability target',
  cost: 'Cost',
  frontier: 'Cost versus reliability',
  sizing: 'Equipment sizing',
  redundancy: 'Spare-equipment options',
  levers: 'Design options',
  dtc: 'Running without the grid',
  fmea_top: 'Top risks',
  tea: 'Cost of energy',
  certification: 'Reliability check',
}

export const STALE_TEXT =
  'These results are from an earlier study; the network was solved since. Run again to refresh.'

export function openFullReport(): void {
  const ui = useUIStore.getState()
  ui.setSlidePanel('results')
  ui.requestResultsTab('adequacy')
  // The Expert panel opens itself and scrolls its report into view.
  ui.requestEhReport()
}

export function ResultsCard() {
  const { study, running } = useHubStudy()
  const { review } = useHubReview(studyHasResults(study))
  const report = useHubReport(study, running)
  const risks = report ? fmeaTopRows(report).slice(0, 3) : []
  const gaps = report ? notEstablishedNotes(report) : []
  const cost = report?.cost_at_target_eur

  return (
    <CardShell step="results" testId="hub-card-results" title="Results">
      {review?.stale === true && (
        <p data-testid="hub-results-stale"
          className="rounded border border-warn/50 bg-warn/10 px-3 py-2 text-[12px] text-warn">
          {STALE_TEXT}
        </p>
      )}

      {review ? (
        <div className="flex flex-col gap-1">
          <span className="text-[11px] font-semibold uppercase tracking-wide text-muted">
            <Term k="verdict">Verdict</Term>
          </span>
          <p data-testid="hub-results-verdict" className="text-[14px] font-semibold text-text">
            {headline(review, report)}
          </p>
        </div>
      ) : (
        <p data-testid="hub-results-loading" className="text-[12px] text-muted">
          Reading the study's findings…
        </p>
      )}

      {cost != null && (
        <p data-testid="hub-results-cost" className="text-[12px]">
          <Term k="cost_at_target">Cost at your goal</Term>:{' '}
          <span className="font-mono text-text">{fmtCurrency(cost)}</span> per year
        </p>
      )}

      {risks.length > 0 && (
        <div className="flex flex-col gap-1 text-[12px]">
          <span className="font-semibold text-text"><Term k="top_risks">Biggest risks</Term></span>
          <ol data-testid="hub-results-risks" className="ml-4 list-decimal">
            {risks.map((r, i) => (
              <li key={r.mode_id} data-testid={`hub-results-risks-${i}`}>
                {r.name || r.mode_id}
                {r.criticality_eur_per_year != null && (
                  <span className="text-muted"> — about {fmtCurrency(r.criticality_eur_per_year)} per year</span>
                )}
              </li>
            ))}
          </ol>
        </div>
      )}

      {gaps.length > 0 && (
        <div className="flex flex-col gap-1 text-[12px]">
          <span className="font-semibold text-text">
            <Term k="not_established">What the study could not establish</Term>
          </span>
          <ul data-testid="hub-results-gaps" className="ml-4 list-disc">
            {gaps.map(g => (
              <li key={g.name}>
                <span className="text-text">{SECTION_LABEL[g.name] ?? 'Another part of the study'}</span>
                <span className="text-muted"> — {g.note}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <button type="button" data-testid="hub-results-open-report" onClick={openFullReport}
        className="self-start inline-flex items-center gap-1.5 rounded border border-border px-3 py-1.5 text-[12px] text-text hover:border-accent hover:text-accent">
        <FileText size={13} /> Open full report
      </button>
    </CardShell>
  )
}
