/**
 * `TemplatePicker` (WP11) — the viewer's template control: a select over
 * the Project's `report_template` uploads plus "Built-in default", an
 * "Upload template…" file input (`.docx`, 25 MB) that uploads with
 * `kind=report_template` and binds the result, and the outline summary the
 * bind route answers with. Refusals become toasts with the template copy.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import type { ReportTemplateState, TemplateOutline } from '../../api/reports'
import type { UploadMeta } from '../../api/uploads'
import { REPORT_TEMPLATE_KEY } from './useReportJob'
import { TemplateOutlineSummary, TemplatePicker } from './TemplatePicker'

const api = vi.hoisted(() => ({
  getReportTemplate: vi.fn(),
  setReportTemplate: vi.fn(),
}))
vi.mock('../../api/reports', async () => {
  const real = await vi.importActual<typeof import('../../api/reports')>('../../api/reports')
  return { ...real, ...api }
})

const uploads = vi.hoisted(() => ({
  listUploads: vi.fn(),
  uploadFile: vi.fn(),
}))
vi.mock('../../api/uploads', async () => {
  const real = await vi.importActual<typeof import('../../api/uploads')>('../../api/uploads')
  return { ...real, ...uploads }
})

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toast }))

const REPORT_ID = 'a1b2c3d4e5f60718'

function upload(over: Partial<UploadMeta> = {}): UploadMeta {
  return {
    schema_version: 1,
    file_id: 'f1',
    filename: 'corporate.docx',
    mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    size: 12_000,
    sha256: 'abc',
    kind: 'report_template',
    uploaded_at: Date.UTC(2026, 8, 28, 10, 0, 0) / 1000,
    blob_ready: true,
    version: 1,
    ...over,
  }
}

const OUTLINE: TemplateOutline = {
  mode: 'untagged',
  language: 'de',
  headings: [
    { index: 0, level: 0, text: 'Bericht', style: 'Title', is_body_start: false },
    { index: 5, level: 1, text: 'Zusammenfassung', style: 'Heading 1', is_body_start: true },
    { index: 9, level: 1, text: 'Kosten', style: 'Heading 1', is_body_start: false },
  ],
  tags: [],
  placeholders: [{ text: '[Client name]', paragraph_index: 2 }],
  tables: [],
  header_text: 'ACME',
  footer_text: '',
  body_start_index: 5,
  n_paragraphs: 16,
  has_toc: true,
  unsupported: ['text box', 'content control'],
}

const UNBOUND: ReportTemplateState = { template_file_id: null, mode: null, language: null, outline: null, plan: null }
const BOUND: ReportTemplateState = { template_file_id: 'f1', mode: 'untagged', language: 'de', outline: OUTLINE, plan: null }

/** The viewer's shape: one query for the binding, the picker and the summary read it. */
function Harness() {
  const template = useQuery({
    queryKey: REPORT_TEMPLATE_KEY('Demo', REPORT_ID),
    queryFn: () => api.getReportTemplate('Demo', REPORT_ID) as Promise<ReportTemplateState>,
    retry: false,
  })
  return (
    <div>
      <TemplatePicker project="Demo" reportId={REPORT_ID} binding={template.data} />
      <TemplateOutlineSummary binding={template.data} />
    </div>
  )
}

function renderPicker() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>)
  return client
}

beforeEach(() => {
  api.getReportTemplate.mockReset().mockResolvedValue(UNBOUND)
  api.setReportTemplate.mockReset()
  uploads.listUploads.mockReset().mockResolvedValue([
    upload(),
    upload({ file_id: 'f2', filename: 'tagged.docx', uploaded_at: Date.UTC(2026, 8, 27, 9, 0, 0) / 1000 }),
  ])
  uploads.uploadFile.mockReset()
  toast.success.mockReset()
  toast.error.mockReset()
})

afterEach(() => cleanup())

describe('TemplatePicker', () => {
  it('lists the report_template uploads (filename + date) after "Built-in default" and reflects the binding', async () => {
    renderPicker()
    const select = await screen.findByLabelText('Template') as HTMLSelectElement
    await waitFor(() => expect(select.options).toHaveLength(3))
    expect(uploads.listUploads).toHaveBeenCalledWith('Demo', 'report_template')
    expect(select.options[0].value).toBe('')
    expect(select.options[0].textContent).toBe('Built-in default')
    expect(select.options[1].value).toBe('f1')
    expect(select.options[1].textContent).toContain('corporate.docx')
    expect(select.options[1].textContent).toContain('2026')
    expect(select.value).toBe('')
    expect(screen.queryByTestId('template-outline')).toBeNull()
  })

  it('binds a chosen template, then shows the outline summary from the bind response', async () => {
    api.setReportTemplate.mockResolvedValue({ ...BOUND, plan: undefined })
    const user = userEvent.setup()
    renderPicker()
    const select = await screen.findByLabelText('Template') as HTMLSelectElement
    await waitFor(() => expect(select.options).toHaveLength(3))
    api.getReportTemplate.mockResolvedValue(BOUND)
    await user.selectOptions(select, 'f1')
    await waitFor(() => expect(api.setReportTemplate).toHaveBeenCalledWith('Demo', REPORT_ID, 'f1'))
    const outline = await screen.findByTestId('template-outline')
    expect(within(outline).getByTestId('template-mode').textContent).toMatch(/untagged/i)
    expect(outline.textContent).toMatch(/language.*de/i)
    expect(outline.textContent).toMatch(/16 paragraphs/)
    expect(outline.textContent).toMatch(/3 headings/)
    expect(outline.textContent).toMatch(/TOC.*yes/i)
    const warn = within(outline).getByTestId('template-unsupported')
    expect(warn.textContent).toContain('text box')
    expect(warn.textContent).toContain('content control')
    expect(warn.textContent).toMatch(/kept where the writer does not touch them/i)
    expect(toast.success).toHaveBeenCalled()
    await waitFor(() => expect(select.value).toBe('f1'))
  })

  it('unbinds back to the built-in default with {file_id: null}', async () => {
    api.getReportTemplate.mockResolvedValue(BOUND)
    api.setReportTemplate.mockResolvedValue(UNBOUND)
    const user = userEvent.setup()
    renderPicker()
    const select = await screen.findByLabelText('Template') as HTMLSelectElement
    await waitFor(() => expect(select.value).toBe('f1'))
    await screen.findByTestId('template-outline')
    api.getReportTemplate.mockResolvedValue(UNBOUND)
    await user.selectOptions(select, '')
    await waitFor(() => expect(api.setReportTemplate).toHaveBeenCalledWith('Demo', REPORT_ID, null))
    await waitFor(() => expect(screen.queryByTestId('template-outline')).toBeNull())
    expect(select.value).toBe('')
  })

  it('uploads a .docx with kind=report_template, refreshes the list and binds the new upload', async () => {
    const created = upload({ file_id: 'f9', filename: 'new.docx' })
    uploads.uploadFile.mockResolvedValue(created)
    api.setReportTemplate.mockResolvedValue({ ...BOUND, template_file_id: 'f9' })
    const user = userEvent.setup()
    renderPicker()
    await screen.findByLabelText('Template')
    uploads.listUploads.mockResolvedValue([created, upload()])
    const file = new File(['PK'], 'new.docx', {
      type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    })
    const input = screen.getByTestId('template-file-input') as HTMLInputElement
    expect(input.accept).toBe('.docx')
    await user.upload(input, file)
    await waitFor(() => expect(uploads.uploadFile).toHaveBeenCalledWith('Demo', file, 'report_template'))
    await waitFor(() => expect(api.setReportTemplate).toHaveBeenCalledWith('Demo', REPORT_ID, 'f9'))
    await waitFor(() => expect(uploads.listUploads).toHaveBeenCalledTimes(2))
    expect(screen.getByRole('button', { name: /upload template/i })).toBeTruthy()
  })

  it('refuses a non-.docx or an oversize file before uploading', async () => {
    // `applyAccept` is a setup option in user-event 14: off, so the browser's
    // own `accept` filter does not hide the component's check.
    const user = userEvent.setup({ applyAccept: false })
    renderPicker()
    await screen.findByLabelText('Template')
    const input = screen.getByTestId('template-file-input') as HTMLInputElement
    await user.upload(input, new File(['x'], 'notes.pdf', { type: 'application/pdf' }))
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(String(toast.error.mock.calls[0][0])).toMatch(/\.docx/i)
    expect(uploads.uploadFile).not.toHaveBeenCalled()

    toast.error.mockReset()
    const big = new File(['x'], 'big.docx')
    Object.defineProperty(big, 'size', { value: 26 * 1024 * 1024 })
    await user.upload(input, big)
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(String(toast.error.mock.calls[0][0])).toMatch(/25 MB/)
    expect(uploads.uploadFile).not.toHaveBeenCalled()
  })

  it.each([
    ['template_not_a_template', 400, /not a word template/i],
    ['template_unreadable', 400, /could not be read/i],
    ['project_locked', 409, /being edited by another user/i],
  ])('toasts the copy for %s and keeps the select on the previous binding', async (kind, status, copy) => {
    const { ReportsError } = await vi.importActual<typeof import('../../api/reports')>('../../api/reports')
    api.setReportTemplate.mockRejectedValue(new ReportsError(
      { error_kind: kind, message: "'Demo' is being edited by another user." }, status,
    ))
    const user = userEvent.setup()
    renderPicker()
    const select = await screen.findByLabelText('Template') as HTMLSelectElement
    await waitFor(() => expect(select.options).toHaveLength(3))
    await user.selectOptions(select, 'f1')
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(String(toast.error.mock.calls[0][0])).toMatch(copy)
    await waitFor(() => expect(select.value).toBe(''))
    expect(screen.queryByTestId('template-outline')).toBeNull()
  })

  it('shows a tagged template as such, without the unsupported list when there is none', async () => {
    api.getReportTemplate.mockResolvedValue({
      ...BOUND, mode: 'tagged', language: 'en',
      outline: { ...OUTLINE, mode: 'tagged', language: 'en', has_toc: false, unsupported: [] },
    })
    renderPicker()
    const outline = await screen.findByTestId('template-outline')
    expect(within(outline).getByTestId('template-mode').textContent).toMatch(/tagged/i)
    expect(outline.textContent).toMatch(/TOC.*no/i)
    expect(within(outline).queryByTestId('template-unsupported')).toBeNull()
  })
})
