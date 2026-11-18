// The run step and the report step (plan S8; gate S4, S6 and S7 carries).
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import RunStep from './RunStep'
import ReportStep from './ReportStep'
import { report, run, study, tornado } from './__fixtures__/payloads'
import { HELP, ERROR_COPY } from '../../utils/decisionVocabulary'
import type { RunRecord } from '../../api/decisionStudies'

afterEach(() => cleanup())

const urls = { html: '/api/x/report.html', docx: '/api/x/report.docx', xlsx: '/api/x/report.xlsx' }

describe('the run step', () => {
  it('says the whole estimate is charged up front, so a stop still counts it', () => {
    render(<RunStep study={study} run={null} error={null} onStart={vi.fn()} onAbort={vi.fn()} busy={false} />)
    const s = screen.getByTestId('budget-sentence').textContent!
    expect(s).toContain('4 solves')
    expect(s).toContain('charged up front')
    expect(s).toMatch(/stopping the run early still counts the full estimate as used/)
  })

  it('gives words to a RunRefused code the manifest does not list', () => {
    render(<RunStep study={study} run={null} onStart={vi.fn()} onAbort={vi.fn()} busy={false}
      error={{ status: 409, code: 'campaign_budget_exhausted', message: 'the run needs up to 5 solve(s)' }} />)
    const r = screen.getByTestId('refusal')
    expect(r.textContent).toContain(ERROR_COPY.campaign_budget_exhausted.title)
    expect(r.textContent).toContain('the run needs up to 5 solve(s)')
  })

  it('lists each option as solved, running or waiting, and offers a stop while running', () => {
    const live: RunRecord = { ...(run as RunRecord), status: 'running', solved: ['none'], current: 'bess_1h', pending: ['bess_2h', 'bess_4h'] }
    const onAbort = vi.fn()
    render(<RunStep study={study} run={live} error={null} onStart={vi.fn()} onAbort={onAbort} busy={false} />)
    const stages = screen.getByTestId('run-stages')
    expect(stages.querySelector('[data-option="none"]')!.textContent).toContain('Solved')
    expect(stages.querySelector('[data-option="bess_1h"]')!.textContent).toContain('Solving')
    expect(stages.querySelector('[data-option="bess_4h"]')!.textContent).toContain('Waiting')
    screen.getByRole('button', { name: 'Stop the run' }).click()
    expect(onAbort).toHaveBeenCalled()
  })
})

describe('the report step', () => {
  it('shows the stale banner with its reasons in words', () => {
    render(<ReportStep report={{ ...report, stale: true, stale_reasons: ['intake_changed_since_findings'],
      verdict: { state: 'stale', broken: [{ edge: 'input:study:s1/intake', field: null, recorded: 'a', current_field: null, current: 'b', reason: 'changed', needs_run: true }] } }} error={null}
      assembleError={null} run={run} tornado={tornado} onAssemble={vi.fn()} busy={false} urls={urls} />)
    const b = screen.getByTestId('report-stale')
    expect(b.textContent).toContain('out of date')
    expect(b.textContent).toContain(HELP.intake_changed_since_findings)
    expect(screen.getByTestId('report-rerun').textContent).toContain('Run the study again')
    expect(screen.queryByTestId('report-reassemble-hint')).toBeNull()
  })

  // PG-05: the verdict says which edge broke; the reason text is never parsed.
  it('offers assembling again when only the report’s own edge broke, whatever the reason text says', () => {
    render(<ReportStep report={{ ...report, stale: true, stale_reasons: ['report_has_no_findings_hashes'],
      verdict: { state: 'stale', broken: [{ edge: 'input:report:s1/findings_hashes', field: null, recorded: 'a', current_field: null, current: 'b', reason: 'changed', needs_run: false }] } }} error={null}
      assembleError={null} run={run} tornado={tornado} onAssemble={vi.fn()} busy={false} urls={urls} />)
    const b = screen.getByTestId('report-stale')
    expect(screen.queryByTestId('report-rerun')).toBeNull()
    expect(screen.getByTestId('report-reassemble-hint').textContent).toBe('Assemble the report again to bring it up to date.')
    // the verdict's edge names never reach the words (the reason code's own muted line is CodeList's, as before)
    expect(b.textContent).not.toMatch(/needs_run|input:/)
  })

  it('asks for the re-run when the report carries no verdict', () => {
    render(<ReportStep report={{ ...report, stale: true, stale_reasons: ['report_has_no_findings_hashes'], verdict: undefined }}
      error={null} assembleError={null} run={run} tornado={tornado} onAssemble={vi.fn()} busy={false} urls={urls} />)
    expect(screen.getByTestId('report-rerun')).toBeTruthy()
    expect(screen.queryByTestId('report-reassemble-hint')).toBeNull()
  })

  it('asks for a re-run from the verdict, not from the reason text', () => {
    // neither code reads "changed", "copied" or "earlier": only the network edge says a run is needed
    render(<ReportStep report={{ ...report, stale: true, stale_reasons: ['report_has_no_findings_hashes', 'network_edited'],
      verdict: { state: 'stale', broken: [{ edge: 'input:report:s1/findings_hashes', field: null, recorded: 'a', current_field: null, current: 'b', reason: 'changed', needs_run: false }, { edge: 'input:run:fork:f1/network', field: null, recorded: 'a', current_field: null, current: 'b', reason: 'changed', needs_run: true }] } }} error={null}
      assembleError={null} run={run} tornado={tornado} onAssemble={vi.fn()} busy={false} urls={urls} />)
    expect(screen.getByTestId('report-rerun')).toBeTruthy()
    expect(screen.queryByTestId('report-reassemble-hint')).toBeNull()
  })

  it('shows whether the report has an established verdict', () => {
    render(<ReportStep report={{ ...report, available: false }} error={null} assembleError={null}
      run={run} tornado={tornado} onAssemble={vi.fn()} busy={false} urls={urls} />)
    expect(screen.getByTestId('report-available').textContent).toContain('verdict not established')
  })

  it('offers downloads and a print view, never an inline frame', () => {
    const { container } = render(<ReportStep report={report} error={null} assembleError={null}
      run={run} tornado={tornado} onAssemble={vi.fn()} busy={false} urls={urls} />)
    expect(container.querySelector('iframe')).toBeNull()
    const docx = screen.getByRole('link', { name: /Word/ })
    expect(docx.getAttribute('href')).toBe(urls.docx)
    expect(docx.hasAttribute('download')).toBe(true)
    expect(screen.getByRole('link', { name: /print/i }).getAttribute('href')).toBe(urls.html)
  })

  it('has copy for a report never assembled and for a prose refusal', () => {
    render(<ReportStep report={null} error={{ status: 404, code: 'report_never_assembled', message: 'POST the report first' }}
      assembleError={{ status: 422, code: 'report_prose_invalid', message: "section 'economics', paragraph 1: digit" }}
      run={run} tornado={null} onAssemble={vi.fn()} busy={false} urls={urls} />)
    expect(screen.getByTestId('report-never').textContent).toContain(ERROR_COPY.report_never_assembled.title)
    expect(screen.getByTestId('refusal').textContent).toContain(ERROR_COPY.report_prose_invalid.title)
  })

  it.each(['intake_changed_since_run', 'ledger_changed_since_run', 'fork_changed_since_run'])(
    'says a re-run is needed when assembly is refused with %s', code => {
      render(<ReportStep report={report} error={null} assembleError={{ status: 409, code, message: 'm' }}
        run={run} tornado={tornado} onAssemble={vi.fn()} busy={false} urls={urls} />)
      expect(screen.getByTestId('refusal').textContent).toContain(ERROR_COPY[code].title)
      expect(screen.getByTestId('refusal').textContent).toContain('Run the study again')
    })

  it('offers re-assembly when the tornado finished after the report', () => {
    const later = { ...tornado, finished_at: Date.parse(report.generated_at) / 1000 + 120 }
    const onAssemble = vi.fn()
    render(<ReportStep report={report} error={null} assembleError={null} run={run} tornado={later}
      onAssemble={onAssemble} busy={false} urls={urls} />)
    expect(screen.getByTestId('report-reassemble').textContent).toContain('newer')
  })
})
