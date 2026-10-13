// Report (spec §3 screen 12, plan S8; gate S7 carries). The HTML is served as
// an ATTACHMENT under `Content-Security-Policy: sandbox`, so it is a download
// or a print view, never an inline frame. `stale`/`stale_reasons` are
// recomputed by the backend on every read; `available` says whether the
// report's verdict is established. Re-assembly is offered when the run or the
// tornado finished after the report was generated (a tornado does not mark it
// stale, gate S7 [N7]).
import type { DecisionReport, RunRecord, StudyError, TornadoRecord } from '../../api/decisionStudies'
import { errorCopy, helpFor } from '../../utils/decisionVocabulary'
import { reportNeedsReassembly, rerunNeeded } from './decisionModel'
import { Banner, Button, Card, CodeList, Refusal } from './DecisionUi'

export default function ReportStep({ report, error, assembleError, run, tornado, onAssemble, busy, urls }: {
  report: DecisionReport | null
  /** The GET's refusal (404 `report_never_assembled` before the first assembly). */
  error: StudyError | null
  assembleError: StudyError | null
  run: Pick<RunRecord, 'finished_at'> | null
  tornado: Pick<TornadoRecord, 'finished_at'> | null
  onAssemble: () => void
  busy: boolean
  urls: { html: string; docx: string; xlsx: string }
}) {
  const never = !report && error?.code === 'report_never_assembled'
  // Gate U2-WP8a N7: `run_by_an_earlier_version` needs a re-run too.
  const staleNeedsRun = !!report?.stale_reasons.some(r => /changed|copied|earlier/.test(r))
  const newer = reportNeedsReassembly(report, run, tornado)
  return (
    <div className="flex flex-col gap-4">
      <Card title="The decision report" right={
        <Button kind="primary" onClick={onAssemble} disabled={busy}>{report ? 'Assemble again' : 'Assemble the report'}</Button>}>
        {never && (
          <div data-testid="report-never">
            <strong>{errorCopy('report_never_assembled').title}</strong> {errorCopy('report_never_assembled').action}
          </div>
        )}
        {!report && error && !never && <Refusal error={error} testId="report-error" />}
        {assembleError && <Refusal error={assembleError} />}
        {assembleError && rerunNeeded(assembleError.code) && (
          <p className="text-muted">Run the study again from the Run page, then assemble the report.</p>
        )}
        {report && (
          <>
            <p data-testid="report-available">
              Assembled {new Date(report.generated_at).toLocaleString()} ·{' '}
              {report.available ? 'verdict established' : 'verdict not established: the report says what is missing'}
            </p>
            {newer && (
              <Banner tone="info" testId="report-reassemble" title="The findings are newer than this report.">
                <span>A run or a robustness check finished after the report was assembled. Assemble it again to include it.</span>
              </Banner>
            )}
            {report.stale && (
              <Banner tone="warn" testId="report-stale" title="This report is out of date.">
                <CodeList codes={report.stale_reasons} tariffHelp={report.honesty_help} />
                {staleNeedsRun && <span data-testid="report-rerun">Run the study again, then assemble the report.</span>}
              </Banner>
            )}
            <div className="flex flex-wrap gap-2">
              <a href={urls.docx} download className="px-3 py-1.5 rounded border border-border hover:border-accent">Download Word (DOCX)</a>
              <a href={urls.xlsx} download className="px-3 py-1.5 rounded border border-border hover:border-accent">Download the workbook (XLSX)</a>
              <a href={urls.html} download className="px-3 py-1.5 rounded border border-border hover:border-accent">Download the print view (HTML)</a>
            </div>
            <p className="text-[11px] text-muted">
              The print view is a file: open it in your browser and print it, or save it as PDF.
              {report.stale ? ' An out-of-date report carries the same warning inside every file.' : ''}
            </p>
            {report.required_disclosures.length > 0 && (
              <details>
                <summary className="cursor-pointer text-muted">What the report discloses before its first number</summary>
                <ul className="mt-2 flex flex-col gap-1">
                  {report.required_disclosures.map(d => <li key={d.code}>{d.text || helpFor(d.code).text}</li>)}
                </ul>
              </details>
            )}
          </>
        )}
      </Card>
    </div>
  )
}
