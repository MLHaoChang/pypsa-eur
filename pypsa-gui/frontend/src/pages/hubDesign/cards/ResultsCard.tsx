// Step 4 — Results (guided-mode spec §5.3, §5.5): the verdict in one
// sentence, the cost, the three costliest risks and what the study could not
// establish — from GET /results/eh_review and the report the Expert panel
// shows. "Show the engineering detail" goes to that panel; "Download report (.docx)" is the Guided way
// to the document.
import { FileText } from 'lucide-react'
import Kpi from '../../../components/Kpi'
import ResultState from '../../../components/ResultState'
import { useUIStore } from '../../../store/uiStore'
import { fmeaTopRows, notEstablishedNotes } from '../../results/EhReferenceDesignPanel'
import { zeroReasonText } from '../../results/fmea'
import { fmtCurrency } from '../../results/shared'
import { studyHasResults } from '../flow'
import { headline, riskName } from '../headline'
import { useHubDesignStore } from '../hubDesignStore'
import { CardShell } from '../shared/CardShell'
import { Term, TERM_FALLBACK } from '../shared/Term'
import { useDispatchSolved, useHubReport, useHubReview, useHubStudy } from '../useHubData'
import { FROM_STUDY_HINT } from '../chapters'
import { SECTION_LABEL } from '../sectionLabels'
import { STALE_TILE_REASON } from '../chapters'
import { costTileLabel, kpiMoney } from '../plainWords'
import { useQuery } from '@tanstack/react-query'
import { networkApi } from '../../../api/network'
import { nk } from '../../../utils/queryKeys'
import { ChaptersSection } from '../ChaptersSection'
import DownloadReport from '../DownloadReport'


export const STALE_TEXT =
  'These results are from an earlier study; the network was solved since. Run again to refresh.'

/** P33b (D-2): an edit seam was crossed since the study started. Says
 *  "edited", never "changed" — that is all the counter carries. */
export const EDITED_TEXT =
  'The network has been edited since this study, so these results describe the design as it was. Run again to refresh.'

export function openFullReport(): void {
  const ui = useUIStore.getState()
  ui.setSlidePanel('results')
  ui.requestResultsTab('adequacy')
  // The Expert panel opens itself and scrolls its report into view.
  ui.requestEhReport()
}

/** Years the Monte Carlo modelled, from the certification section (UX review N10). */
function horizonYearsOf(report: ReturnType<typeof useHubReport>): number | null {
  const h = report?.sections?.certification?.payload?.horizon_years
  return typeof h === 'number' ? h : null
}

export function ResultsCard() {
  const { study, running } = useHubStudy()
  const { review } = useHubReview(studyHasResults(study))
  const report = useHubReport(study, running)
  const solved = useDispatchSolved()
  // A unit whose loss causes no shortfall is not a risk (UX S6, G3): it is
  // named once under the list, never ranked in it.
  const rows = report ? fmeaTopRows(report) : []
  const isNoShortfall = (r: (typeof rows)[number]) =>
    r.zero_reason === 'no_shortfall' && !r.criticality_eur_per_year
  const risks = rows.filter(r => !isNoShortfall(r)).slice(0, 3)
  const coped = rows.filter(isNoShortfall).map(r => riskName(r.name || r.mode_id))
  const gaps = report ? notEstablishedNotes(report) : []
  const cost = report?.cost_at_target_eur
  const project = useUIStore(s => s.currentProject)
  const multiPeriod = report?.period_basis === 'npv_multi_period'
  const periods = useQuery({
    queryKey: nk(project, 'investmentPeriods'), queryFn: networkApi.getInvestmentPeriods,
    enabled: !!project && multiPeriod,
  })
  const setStep = useHubDesignStore(s => s.setStep)
  // The four category tiles (UX S6, G4/G6), shown whether or not the study ran.
  const summary = review?.summary ?? {}
  const verdict = summary.verdict
  // Out-of-date figures are marked on the tiles too, not only above them (UX review N4, L5).
  const stale = review?.stale === true && study?.edited_since_study !== true
  const chapters = (
    <ChaptersSection reliability={{
      hasResults: studyHasResults(study), running,
      lole: summary.mc_lole_h_per_year ?? report?.mc_lole_h ?? null,
      target: summary.target_lole_h ?? null,
      horizonYears: horizonYearsOf(report),
      stale,
      verdict: verdict === 'pass' || verdict === 'fail' || verdict === 'inconclusive' ? verdict : null,
    }} />
  )

  if (!studyHasResults(study)) {
    // Never an empty or greyed card (UX S6, G2): say why, and how to get there.
    return (
      <CardShell step="results" next="improve" testId="hub-card-results" title="Results">
        {running ? (
          <ResultState state="running" reason="The study is running; its verdict, cost and risks appear here when it finishes." />
        ) : (
          <ResultState state="not_run" reason="The study writes its verdict, cost and risks here."
            action={{ label: 'Go to Goal and run the study', onClick: () => setStep('goal', { user: true }) }} />
        )}
        {chapters}
      </CardShell>
    )
  }

  return (
    <CardShell step="results" next="improve" testId="hub-card-results" title="Results">
      {/* One amber line (D-3): the edited fact subsumes solved-since. */}
      {study?.edited_since_study === true && (
        <p data-testid="hub-results-edited"
          className="rounded border border-warn/50 bg-warn/10 px-3 py-2 text-[12px] text-warn">
          {EDITED_TEXT}
        </p>
      )}
      {stale && (
        // The shared stale state (UX audit L5), with the way to refresh it (UX review N4).
        <div data-testid="hub-results-stale"><ResultState state="stale" reason={STALE_TEXT}
          action={{ label: 'Go to Goal and run the study', onClick: () => setStep('goal', { user: true }) }} /></div>
      )}

      {review ? (
        <div className="flex flex-col gap-1">
          <span className="text-[11px] font-semibold uppercase tracking-wide text-muted">
            <Term k="verdict">Verdict</Term>
          </span>
          <p data-testid="hub-results-verdict" className="text-[14px] font-semibold text-text">
            {headline(review, report)}
          </p>
          {/* P26 gate: with no goal the headline asks for one — take the user there. */}
          {review.status === 'ok' && review.summary?.target_lole_h == null && (
            <button type="button" data-testid="hub-results-set-goal"
              onClick={() => setStep('goal', { user: true })}
              className="self-start mt-1 inline-flex items-center gap-1 rounded bg-accent px-3 py-1.5 text-[12px] font-semibold text-white">
              Set a goal
            </button>
          )}
        </div>
      ) : (
        <p data-testid="hub-results-loading" className="text-[12px] text-muted">
          Reading the study's findings…
        </p>
      )}

      {cost != null && (
        // The cost of the planned design at the pack's energy target, without
        // shortfall costs — never "the cost of your goal" (P24-FE gate B1).
        // The shared KPI tile (UX S6, G-item 2), the glossary text on hover.
        <div className="max-w-[260px]">
          <Kpi testId="hub-results-cost" headline
            label={costTileLabel(report?.period_basis, periods.data?.periods)}
            value={fmtCurrency(cost)} fromStudy={solved === false && !stale}
            hint={solved === false && !stale ? `${TERM_FALLBACK.cost_at_target} ${FROM_STUDY_HINT}` : TERM_FALLBACK.cost_at_target}
            state={stale ? 'stale' : 'ready'} reason={stale ? STALE_TILE_REASON : undefined} />
        </div>
      )}

      {(risks.length > 0 || coped.length > 0) && (
        <div className="flex flex-col gap-1 text-[12px]">
          <span className="font-semibold text-text"><Term k="top_risks">Biggest risks</Term></span>
          {risks.length > 0 && <ol data-testid="hub-results-risks" className="ml-4 list-decimal">
            {risks.map((r, i) => (
              <li key={r.mode_id} data-testid={`hub-results-risks-${i}`}>
                {riskName(r.name || r.mode_id)}
                {r.criticality_eur_per_year != null && (
                  <span className="text-muted">
                    {' — '}{r.criticality_eur_per_year > 0
                      ? `about ${kpiMoney(r.criticality_eur_per_year)} per year`
                      // P29 (B3): the same reason text as the FMEA tab.
                      : (zeroReasonText(r.zero_reason) ?? 'no measurable cost')}
                  </span>
                )}
              </li>
            ))}
          </ol>}
          {coped.length > 0 && (
            <span className="text-muted" data-testid="hub-results-no-shortfall">
              Also checked: {coped.join(', ')} — no shortfall, the site copes without {coped.length > 1 ? 'them' : 'it'}.
            </span>
          )}
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

      <div className="flex flex-wrap items-start gap-2">
        {project && <DownloadReport project={project} />}
        <button type="button" data-testid="hub-results-open-report" onClick={openFullReport}
          className="self-start inline-flex items-center gap-1.5 rounded border border-border px-3 py-1.5 text-[12px] text-text hover:border-accent hover:text-accent">
          <FileText size={13} /> Show the engineering detail
        </button>
      </div>
      {chapters}
    </CardShell>
  )
}
