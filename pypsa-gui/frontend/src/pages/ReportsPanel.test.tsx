/**
 * The Reports panel (WP7a) lists what the backend's list route reports for
 * the active Project and does one request per action: create an
 * evidence-only report (POST, then the list refetches and the new report
 * opens), delete behind a ConfirmDialog, open a report in the viewer.
 * Refusals arrive as toasts carrying the backend's message.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReportDocument, ReportJobRecord, ReportMeta } from '../api/reports'
import ReportsPanel from './ReportsPanel'

const store = vi.hoisted(() => ({
  currentProject: 'Demo' as string | null,
  reportGenerateRequest: false,
  clearReportGenerateRequest: vi.fn(),
}))
vi.mock('../store/uiStore', () => ({
  useUIStore: (sel: (s: typeof store) => unknown) => sel(store),
}))

const api = vi.hoisted(() => ({
  listReports: vi.fn(),
  getReport: vi.fn(),
  createEvidenceOnlyReport: vi.fn(),
  deleteReport: vi.fn(),
  exportReport: vi.fn(),
  getGenerateStatus: vi.fn(),
  generateReport: vi.fn(),
  abortGenerate: vi.fn(),
  regenerateSection: vi.fn(),
  getEvidenceHash: vi.fn(),
}))
vi.mock('../api/reports', async () => {
  const real = await vi.importActual<typeof import('../api/reports')>('../api/reports')
  return { ...real, ...api }
})

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toast }))

const ID_A = 'a1b2c3d4e5f60718'
const ID_B = 'b1b2c3d4e5f60718'

const META_A: ReportMeta = {
  report_id: ID_A,
  title: 'Study report — Demo',
  created_at: '2026-09-28T10:00:00+00:00',
  updated_at: '2026-09-28T10:00:00+00:00',
  latest_version: 2,
  mode: 'evidence_only',
  evidence_hash: 'deadbeef',
  profile_id: null,
  model: null,
}
const META_B: ReportMeta = {
  ...META_A,
  report_id: ID_B,
  title: 'Client report',
  latest_version: 1,
  mode: 'generated',
  profile_id: 'anthropic-default',
  model: 'claude-sonnet',
}

const DOC_A: ReportDocument = {
  schema_version: 1,
  report_id: ID_A,
  version: 2,
  title: META_A.title,
  language: 'en',
  created_at: META_A.created_at,
  evidence_hash: 'deadbeef',
  profile_id: null,
  model: null,
  mode: 'evidence_only',
  template_file_id: null,
  sections: [{
    section_id: 'summary', heading: 'Executive summary', source: 'code', status: 'ok',
    blocks: [{ type: 'paragraph', md: 'Hello.' }], note: null, audit: { unverified: [], verified: [] },
  }],
  tables: {},
  figures: {},
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <ReportsPanel />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  store.currentProject = 'Demo'
  api.listReports.mockReset().mockResolvedValue([])
  api.getReport.mockReset().mockResolvedValue(DOC_A)
  api.createEvidenceOnlyReport.mockReset()
  api.deleteReport.mockReset().mockResolvedValue({ deleted: true, report_id: ID_A })
  api.exportReport.mockReset()
  api.getGenerateStatus.mockReset().mockResolvedValue(null)
  api.generateReport.mockReset().mockResolvedValue({ status: 'running', report_id: ID_B })
  api.abortGenerate.mockReset().mockResolvedValue({ status: 'running', aborting: true })
  api.regenerateSection.mockReset()
  // The route's hash matches the fixture document by default: no badge.
  api.getEvidenceHash.mockReset().mockResolvedValue({ evidence_hash: 'deadbeef', sections_ok: 1, sections_total: 21 })
  store.reportGenerateRequest = false
  store.clearReportGenerateRequest.mockReset()
  toast.success.mockReset()
  toast.error.mockReset()
})

function job(over: Partial<ReportJobRecord> = {}): ReportJobRecord {
  return {
    status: 'running',
    report_id: ID_B,
    version: null,
    mode: 'generate',
    section: null,
    progress: { done: 1, total: 4, current: 'fmea_top' },
    repairs: 0,
    prose_failures: [],
    error: null,
    started_at: Date.now() / 1000 - 65,
    finished_at: null,
    profile_id: 'anthropic-default',
    model: 'claude-sonnet',
    ...over,
  }
}

afterEach(() => cleanup())

describe('ReportsPanel', () => {
  it('asks for a Project when none is open and disables Create with the reason', () => {
    store.currentProject = null
    renderPanel()
    expect(screen.getByText(/Open a Project/i)).toBeTruthy()
    const create = screen.getByTestId('reports-create') as HTMLButtonElement
    expect(create.disabled).toBe(true)
    expect(create.title).toMatch(/no project/i)
    expect(api.listReports).not.toHaveBeenCalled()
  })

  it('shows the empty state when the Project has no reports', async () => {
    renderPanel()
    expect((await screen.findByTestId('reports-empty')).textContent).toMatch(/no reports yet/i)
    expect(api.listReports).toHaveBeenCalledWith('Demo')
  })

  it('lists reports with title, version, mode, created date and profile/model when set', async () => {
    api.listReports.mockResolvedValue([META_A, META_B])
    renderPanel()
    const rows = await screen.findAllByTestId('report-row')
    expect(rows).toHaveLength(2)
    const a = within(rows[0])
    expect(a.getByText('Study report — Demo')).toBeTruthy()
    expect(a.getByText('v2')).toBeTruthy()
    expect(a.getByText(/evidence only/i)).toBeTruthy()
    expect(a.queryByText('anthropic-default')).toBeNull()
    const b = within(rows[1])
    expect(b.getByText('Client report')).toBeTruthy()
    expect(b.getByText('v1')).toBeTruthy()
    expect(b.getByText(/generated/i)).toBeTruthy()
    expect(b.getByText(/anthropic-default/)).toBeTruthy()
    expect(b.getByText(/claude-sonnet/)).toBeTruthy()
    expect(rows[0].textContent).toContain('2026')
  })

  it('creates an evidence-only report, refreshes the list and opens the new report', async () => {
    api.createEvidenceOnlyReport.mockResolvedValue({ ...META_A, document: DOC_A })
    api.listReports.mockResolvedValueOnce([]).mockResolvedValue([META_A])
    const user = userEvent.setup()
    renderPanel()
    await screen.findByTestId('reports-empty')
    await user.click(screen.getByTestId('reports-create'))
    await waitFor(() => expect(api.createEvidenceOnlyReport).toHaveBeenCalledWith('Demo'))
    await waitFor(() => expect(api.listReports).toHaveBeenCalledTimes(2))
    // the viewer opened on the new report
    expect((await screen.findAllByTestId('report-section'))).toHaveLength(1)
    expect(api.getReport).toHaveBeenCalledWith('Demo', ID_A, undefined)
    expect(toast.success).toHaveBeenCalled()
  })

  it('toasts the backend message when creation is refused', async () => {
    const { ReportsError } = await vi.importActual<typeof import('../api/reports')>('../api/reports')
    api.createEvidenceOnlyReport.mockRejectedValue(new ReportsError(
      { error_kind: 'report_mode_not_supported', message: 'Use mode evidence_only.' }, 400,
    ))
    const user = userEvent.setup()
    renderPanel()
    await screen.findByTestId('reports-empty')
    await user.click(screen.getByTestId('reports-create'))
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(String(toast.error.mock.calls[0][0])).toContain('Use mode evidence_only.')
  })

  it('deletes only after the ConfirmDialog is confirmed, then refreshes the list', async () => {
    api.listReports.mockResolvedValueOnce([META_A]).mockResolvedValue([])
    const user = userEvent.setup()
    renderPanel()
    const row = (await screen.findAllByTestId('report-row'))[0]
    await user.click(within(row).getByTestId('report-delete'))
    const dialog = await screen.findByRole('dialog')
    expect(api.deleteReport).not.toHaveBeenCalled()
    await user.click(within(dialog).getByText('Cancel'))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(api.deleteReport).not.toHaveBeenCalled()

    await user.click(within(row).getByTestId('report-delete'))
    await user.click(within(await screen.findByRole('dialog')).getByText('Delete report'))
    await waitFor(() => expect(api.deleteReport).toHaveBeenCalledWith('Demo', ID_A))
    await waitFor(() => expect(api.listReports).toHaveBeenCalledTimes(2))
    expect(await screen.findByTestId('reports-empty')).toBeTruthy()
  })

  it('toasts the lock refusal on delete', async () => {
    const { ReportsError } = await vi.importActual<typeof import('../api/reports')>('../api/reports')
    api.listReports.mockResolvedValue([META_A])
    api.deleteReport.mockRejectedValue(new ReportsError(
      { error_kind: 'project_locked', message: "'Demo' is being edited by another user." }, 409,
    ))
    const user = userEvent.setup()
    renderPanel()
    const row = (await screen.findAllByTestId('report-row'))[0]
    await user.click(within(row).getByTestId('report-delete'))
    await user.click(within(await screen.findByRole('dialog')).getByText('Delete report'))
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(String(toast.error.mock.calls[0][0])).toContain('being edited by another user')
  })

  it('opens the viewer when a report is selected and returns to the list on Back', async () => {
    api.listReports.mockResolvedValue([META_A])
    const user = userEvent.setup()
    renderPanel()
    const row = (await screen.findAllByTestId('report-row'))[0]
    await user.click(within(row).getByTestId('report-open'))
    await screen.findAllByTestId('report-section')
    expect(api.getReport).toHaveBeenCalledWith('Demo', ID_A, undefined)
    await user.click(screen.getByTestId('report-back'))
    expect(await screen.findAllByTestId('report-row')).toHaveLength(1)
  })

  it('reads the current evidence hash from the route when the panel mounts', async () => {
    api.listReports.mockResolvedValue([META_A])
    renderPanel()
    await screen.findAllByTestId('report-row')
    await waitFor(() => expect(api.getEvidenceHash).toHaveBeenCalledWith('Demo'))
  })

  it('the viewer badge compares against the route hash, not the newest evidence-only report', async () => {
    // The newest evidence-only report carries the SAME hash as the opened
    // document (`deadbeef`): the old derivation would show no badge. The
    // route says the live evidence differs → the badge shows.
    api.listReports.mockResolvedValue([META_A])
    api.getEvidenceHash.mockResolvedValue({ evidence_hash: 'cafebabe', sections_ok: 2, sections_total: 21 })
    const user = userEvent.setup()
    renderPanel()
    const row = (await screen.findAllByTestId('report-row'))[0]
    await user.click(within(row).getByTestId('report-open'))
    await screen.findAllByTestId('report-section')
    const badge = await screen.findByTestId('evidence-changed')
    expect(badge.textContent).toMatch(/evidence changed since v2/i)
    cleanup()

    // Equal hashes → no badge.
    api.getEvidenceHash.mockResolvedValue({ evidence_hash: 'deadbeef', sections_ok: 2, sections_total: 21 })
    renderPanel()
    const row2 = (await screen.findAllByTestId('report-row'))[0]
    await user.click(within(row2).getByTestId('report-open'))
    await screen.findAllByTestId('report-section')
    await waitFor(() => expect(api.getEvidenceHash).toHaveBeenCalledTimes(2))
    expect(screen.queryByTestId('evidence-changed')).toBeNull()
  })

  it('shows the list error inline rather than a blank panel', async () => {
    const { ReportsError } = await vi.importActual<typeof import('../api/reports')>('../api/reports')
    api.listReports.mockRejectedValue(new ReportsError(
      { error_kind: 'unknown', message: 'reports request failed: 502 Bad Gateway' }, 502,
    ))
    renderPanel()
    expect((await screen.findByTestId('reports-error')).textContent).toContain('502')
  })
})

describe('ReportsPanel — generation (WP7b)', () => {
  it('disables Generate with the reason when no Project is open', () => {
    store.currentProject = null
    renderPanel()
    const gen = screen.getByTestId('reports-generate') as HTMLButtonElement
    expect(gen.disabled).toBe(true)
    expect(gen.title).toMatch(/no project/i)
  })

  it('opens the Generate dialog and starts the job with the submitted options', async () => {
    api.listReports.mockResolvedValue([META_A])
    const user = userEvent.setup()
    renderPanel()
    await screen.findAllByTestId('report-row')
    expect(screen.queryByRole('dialog')).toBeNull()
    await user.click(screen.getByTestId('reports-generate'))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Title'), 'Client report')
    api.getGenerateStatus.mockResolvedValue(job())
    await user.click(within(dialog).getByTestId('generate-submit'))
    await waitFor(() => expect(api.generateReport).toHaveBeenCalledWith('Demo', { title: 'Client report', language: 'en' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    // the strip appears once the status says running
    expect(await screen.findByTestId('report-job-strip')).toBeTruthy()
  })

  it('offers the sections of the newest evidence-only report in the dialog', async () => {
    api.listReports.mockResolvedValue([META_B, META_A])
    const user = userEvent.setup()
    renderPanel()
    await screen.findAllByTestId('report-row')
    await user.click(screen.getByTestId('reports-generate'))
    const dialog = await screen.findByRole('dialog')
    await waitFor(() => expect(api.getReport).toHaveBeenCalledWith('Demo', ID_A))
    await waitFor(() => expect(within(dialog).getAllByRole('checkbox').map(b => (b as HTMLInputElement).value)).toEqual(['summary']))
  })

  it('pre-armed generation opens the dialog on arrival and clears the request', async () => {
    store.reportGenerateRequest = true
    renderPanel()
    expect(await screen.findByRole('dialog')).toBeTruthy()
    await waitFor(() => expect(store.clearReportGenerateRequest).toHaveBeenCalled())
  })

  it('shows the progress strip while running: done/total, the current section title, elapsed, and Abort', async () => {
    api.listReports.mockResolvedValue([META_B])
    api.getGenerateStatus.mockResolvedValue(job())
    renderPanel()
    const strip = await screen.findByTestId('report-job-strip')
    expect(strip.textContent).toContain('1/4')
    expect(strip.textContent).toContain('Residual failure modes')
    expect(strip.textContent).toMatch(/1:0\d elapsed/)
    const bar = within(strip).getByRole('progressbar')
    expect(bar.getAttribute('aria-valuenow')).toBe('25')
    // the Generate button is disabled while a job runs
    expect((screen.getByTestId('reports-generate') as HTMLButtonElement).disabled).toBe(true)
  })

  it('Abort calls the abort route and shows "stopping…" until the record leaves running', async () => {
    api.listReports.mockResolvedValue([META_B])
    api.getGenerateStatus.mockResolvedValue(job())
    const user = userEvent.setup()
    renderPanel()
    const strip = await screen.findByTestId('report-job-strip')
    await user.click(within(strip).getByTestId('report-job-abort'))
    await waitFor(() => expect(api.abortGenerate).toHaveBeenCalledWith('Demo'))
    expect((await screen.findByTestId('report-job-abort')).textContent).toMatch(/stopping/i)
  })

  it('shows the failed banner with the error', async () => {
    api.listReports.mockResolvedValue([META_B])
    api.getGenerateStatus.mockResolvedValue(job({ status: 'failed', error: 'provider exploded', finished_at: Date.now() / 1000 }))
    renderPanel()
    const banner = await screen.findByTestId('report-job-failed')
    expect(banner.textContent).toContain('provider exploded')
    expect(screen.queryByTestId('report-job-abort')).toBeNull()
  })

  it('shows the done line with profile, model and repairs, and lists prose failures by section', async () => {
    api.listReports.mockResolvedValue([META_B])
    api.getGenerateStatus.mockResolvedValue(job({
      status: 'done', version: 1, repairs: 2,
      progress: { done: 4, total: 4, current: null }, finished_at: Date.now() / 1000,
      prose_failures: [
        { section_id: 'gates', reason: 'invalid_json', raw_head: '{"x":', repairs: 1 },
        { section_id: 'tea', reason: 'empty', raw_head: null, repairs: 0 },
      ],
    }))
    renderPanel()
    const done = await screen.findByTestId('report-job-done')
    expect(done.textContent).toContain('anthropic-default')
    expect(done.textContent).toContain('claude-sonnet')
    expect(done.textContent).toMatch(/2 repairs/)
    const failures = screen.getAllByTestId('report-job-prose-failure')
    expect(failures).toHaveLength(2)
    expect(failures[0].textContent).toContain('Dynamics gates')
    expect(failures[0].textContent).toContain('invalid_json')
    expect(failures[1].textContent).toContain('Techno-economic')
  })

  it('the list shows the mode and a "written by <model>" chip on generated reports', async () => {
    api.listReports.mockResolvedValue([META_A, META_B])
    renderPanel()
    const rows = await screen.findAllByTestId('report-row')
    expect(within(rows[0]).queryByTestId('report-written-by')).toBeNull()
    expect(within(rows[1]).getByTestId('report-written-by').textContent).toMatch(/written by claude-sonnet/i)
  })
})
