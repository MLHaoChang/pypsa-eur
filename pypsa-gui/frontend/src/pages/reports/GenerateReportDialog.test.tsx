/**
 * `GenerateReportDialog` (WP7b) — title, language (`en` default, free
 * field), an optional section multi-select, an optional instruction; submit
 * hands the options to `onGenerate` and the backend's refusal kinds become
 * the toast copy the panel promises (`report_job_in_flight`, `no_evidence`,
 * `missing_api_key` / `sdk_not_installed`, `project_locked`).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ReportsError, type ChartSnapshot } from '../../api/reports'
import { resetReportCharts } from '../results/reportCharts'
import { DEFAULT_SECTION_CHOICES, GenerateReportDialog } from './GenerateReportDialog'

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toast }))

const uploads = vi.hoisted(() => ({ listUploads: vi.fn() }))
vi.mock('../../api/uploads', async () => {
  const real = await vi.importActual<typeof import('../../api/uploads')>('../../api/uploads')
  return { ...real, ...uploads }
})

const charts = vi.hoisted(() => ({ listReportCharts: vi.fn(), removeReportChart: vi.fn() }))
vi.mock('../../api/reports', async () => {
  const real = await vi.importActual<typeof import('../../api/reports')>('../../api/reports')
  return { ...real, ...charts }
})

const SNAP = (over: Partial<ChartSnapshot>): ChartSnapshot => ({
  id: 'chart-0123456789abcdef', view: 'v', title: 'Dispatch stack', figure: 'f', unit: null,
  source_line: 'Latest solve · solved', period: null, out_of_date_when_added: false, created_at: 't',
  earlier_solve: false, rows_total: 1, ...over,
})

const TEMPLATE_UPLOAD = {
  schema_version: 1, file_id: 'f1', filename: 'corporate.docx', mime: 'application/x', size: 1, sha256: 'a',
  kind: 'report_template' as const, uploaded_at: 1_790_000_000, blob_ready: true, version: 1,
}

function renderDialog(over: Partial<React.ComponentProps<typeof GenerateReportDialog>> = {}) {
  const onGenerate = vi.fn().mockResolvedValue({ status: 'running', report_id: 'a1b2c3d4e5f60718' })
  const onClose = vi.fn()
  render(
    <GenerateReportDialog open onClose={onClose} onGenerate={onGenerate} {...over} />,
  )
  return { onGenerate, onClose }
}

beforeEach(() => {
  toast.success.mockReset()
  toast.error.mockReset()
  resetReportCharts()
  charts.listReportCharts.mockReset().mockResolvedValue({ charts: [], limit: 12 })
  charts.removeReportChart.mockReset().mockResolvedValue(undefined)
  uploads.listUploads.mockReset().mockResolvedValue([TEMPLATE_UPLOAD])
})

afterEach(() => cleanup())

describe('GenerateReportDialog', () => {
  it('lists the fixed section catalogue (executive summary + the 12 EH sections) when no document is given', () => {
    renderDialog()
    const dialog = screen.getByRole('dialog')
    const boxes = within(dialog).getAllByRole('checkbox') as HTMLInputElement[]
    expect(boxes.map(b => b.value)).toEqual(DEFAULT_SECTION_CHOICES.map(c => c.id))
    expect(DEFAULT_SECTION_CHOICES.map(c => c.id)).toEqual([
      'executive_summary', 'target', 'certification', 'cost', 'frontier', 'sizing', 'redundancy',
      'levers', 'dtc', 'fmea_top', 'tea', 'gates', 'multi_energy',
    ])
    expect(within(dialog).getByLabelText(/Residual failure modes/)).toBeTruthy()
    expect(within(dialog).getByLabelText(/Executive summary/)).toBeTruthy()
    // none checked → "all sections"
    expect(boxes.every(b => !b.checked)).toBe(true)
  })

  it('offers the sections of the given document instead of the catalogue', () => {
    renderDialog({ sectionChoices: [{ id: 'summary', title: 'Summary' }, { id: 'gates', title: 'Gates' }] })
    const boxes = screen.getAllByRole('checkbox') as HTMLInputElement[]
    expect(boxes.map(b => b.value)).toEqual(['summary', 'gates'])
  })

  it('submits the defaults: language en, no title, no sections, no instruction', async () => {
    const user = userEvent.setup()
    const { onGenerate, onClose } = renderDialog()
    expect((screen.getByLabelText('Language') as HTMLInputElement).value).toBe('en')
    await user.click(screen.getByTestId('generate-submit'))
    await waitFor(() => expect(onGenerate).toHaveBeenCalledTimes(1))
    expect(onGenerate).toHaveBeenCalledWith({ language: 'en' })
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(toast.success).toHaveBeenCalled()
  })

  it('submits title, a free-form language, the picked sections and the instruction', async () => {
    const user = userEvent.setup()
    const { onGenerate } = renderDialog()
    await user.type(screen.getByLabelText('Title'), 'Client report')
    const lang = screen.getByLabelText('Language')
    await user.clear(lang)
    await user.type(lang, 'de')
    await user.click(screen.getByLabelText(/Cost at target/))
    await user.click(screen.getByLabelText(/Residual failure modes/))
    await user.type(screen.getByLabelText('Instruction'), 'Keep it short.')
    await user.click(screen.getByTestId('generate-submit'))
    await waitFor(() => expect(onGenerate).toHaveBeenCalledWith({
      title: 'Client report', language: 'de', sections: ['cost', 'fmea_top'], instruction: 'Keep it short.',
    }))
  })

  it('does not submit an empty language', async () => {
    const user = userEvent.setup()
    const { onGenerate } = renderDialog()
    await user.clear(screen.getByLabelText('Language'))
    await user.click(screen.getByTestId('generate-submit'))
    expect(onGenerate).not.toHaveBeenCalled()
    expect(screen.getByTestId('generate-error').textContent).toMatch(/language/i)
  })

  it.each([
    ['report_job_in_flight', 409, /already being written/i],
    ['no_evidence', 400, /run a study first/i],
    ['missing_api_key', 400, /configure an LLM profile in Settings/i],
    ['sdk_not_installed', 400, /configure an LLM profile in Settings/i],
    ['project_locked', 409, /being edited by another user/i],
  ])('toasts the copy for %s and stays open', async (kind, status, copy) => {
    const user = userEvent.setup()
    const { onGenerate, onClose } = renderDialog()
    onGenerate.mockRejectedValue(new ReportsError(
      { error_kind: kind, message: "'Demo' is being edited by another user." }, status,
    ))
    await user.click(screen.getByTestId('generate-submit'))
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(String(toast.error.mock.calls[0][0])).toMatch(copy)
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).toBeTruthy()
  })

  it('closes on Cancel without generating', async () => {
    const user = userEvent.setup()
    const { onGenerate, onClose } = renderDialog()
    await user.click(screen.getByText('Cancel'))
    expect(onClose).toHaveBeenCalled()
    expect(onGenerate).not.toHaveBeenCalled()
  })
})

describe('GenerateReportDialog — template (WP11)', () => {
  it('offers no template select without a project and sends no template_file_id', async () => {
    const user = userEvent.setup()
    const { onGenerate } = renderDialog()
    expect(screen.queryByLabelText('Template')).toBeNull()
    expect(uploads.listUploads).not.toHaveBeenCalled()
    await user.click(screen.getByTestId('generate-submit'))
    await waitFor(() => expect(onGenerate).toHaveBeenCalledWith({ language: 'en' }))
  })

  it('lists the Project\'s report templates after "Built-in default" and sends the chosen template_file_id', async () => {
    const user = userEvent.setup()
    const { onGenerate } = renderDialog({ project: 'Demo' })
    const select = await screen.findByLabelText('Template') as HTMLSelectElement
    await waitFor(() => expect(select.options).toHaveLength(2))
    expect(uploads.listUploads).toHaveBeenCalledWith('Demo', 'report_template')
    expect(select.options[0].textContent).toBe('Built-in default')
    expect(select.options[1].value).toBe('f1')
    expect(select.options[1].textContent).toContain('corporate.docx')
    await user.selectOptions(select, 'f1')
    // no language known for it → the language stays as it was
    expect((screen.getByLabelText('Language') as HTMLInputElement).value).toBe('en')
    await user.click(screen.getByTestId('generate-submit'))
    await waitFor(() => expect(onGenerate).toHaveBeenCalledWith({ language: 'en', template_file_id: 'f1' }))
  })

  it('pre-fills the language from the template\'s detected language once known, and keeps it when going back to the default', async () => {
    const user = userEvent.setup()
    const { onGenerate } = renderDialog({ project: 'Demo', templateLanguages: { f1: 'de' } })
    const select = await screen.findByLabelText('Template') as HTMLSelectElement
    await waitFor(() => expect(select.options).toHaveLength(2))
    await user.selectOptions(select, 'f1')
    const lang = screen.getByLabelText('Language') as HTMLInputElement
    expect(lang.value).toBe('de')
    await user.selectOptions(select, '')
    expect(lang.value).toBe('de')
    await user.click(screen.getByTestId('generate-submit'))
    await waitFor(() => expect(onGenerate).toHaveBeenCalledWith({ language: 'de' }))
  })

  it('still generates when the template list cannot be read', async () => {
    uploads.listUploads.mockRejectedValue(new Error('offline'))
    const user = userEvent.setup()
    const { onGenerate } = renderDialog({ project: 'Demo' })
    const select = await screen.findByLabelText('Template') as HTMLSelectElement
    await waitFor(() => expect(uploads.listUploads).toHaveBeenCalled())
    expect(select.options).toHaveLength(1)
    await user.click(screen.getByTestId('generate-submit'))
    await waitFor(() => expect(onGenerate).toHaveBeenCalledWith({ language: 'en' }))
  })

  it('lists the charts added from results with notes and removes one', async () => {
    charts.listReportCharts.mockResolvedValue({
      charts: [SNAP({ out_of_date_when_added: true }), SNAP({ id: 'chart-1123456789abcdef', title: 'Prices', earlier_solve: true })],
      limit: 12,
    })
    const user = userEvent.setup()
    renderDialog({ project: 'demo' })
    const list = await screen.findByTestId('report-charts-list')
    await within(list).findByText('Dispatch stack')
    expect(within(list).getByText('Out of date when added')).toBeTruthy()
    expect(within(list).getByText('From an earlier solve')).toBeTruthy()
    expect(list.textContent).not.toMatch(/stale/i)
    await user.click(within(list).getByRole('button', { name: 'Remove Dispatch stack from report' }))
    await waitFor(() => expect(charts.removeReportChart).toHaveBeenCalledWith('demo', 'chart-0123456789abcdef'))
    await waitFor(() => expect(within(list).queryByText('Dispatch stack')).toBeNull())
  })

  it('says in words that no chart was added yet', async () => {
    renderDialog({ project: 'demo' })
    expect((await screen.findByTestId('report-charts-list')).textContent).toMatch(/No charts added yet/)
  })
})
