/**
 * `MappingPlanEditor` (WP11) — the per-heading keep / rename / drop table of
 * an untagged template's outline, the section multi-select per row, the
 * inserted-sections list, the placeholder values, and the read-only
 * `unmapped_sections` / `notes`. "Propose with the assistant" starts the
 * `mode: "mapping"` job through `useReportJob` and the strip shows it;
 * when the job finishes the template state is re-read and the editor
 * shows the proposed plan. "Save plan" PUTs the draft (non-strict) and
 * re-reads; a refused plan is a toast with the backend's notes.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import type { MappingPlan, ReportJobRecord, ReportTemplateState, TemplateOutline } from '../../api/reports'
import { MappingPlanEditor } from './MappingPlanEditor'
import { REPORT_TEMPLATE_KEY, useReportJob } from './useReportJob'

const api = vi.hoisted(() => ({
  getReportTemplate: vi.fn(),
  putMappingPlan: vi.fn(),
  proposeMappingPlan: vi.fn(),
  getGenerateStatus: vi.fn(),
  abortGenerate: vi.fn(),
}))
vi.mock('../../api/reports', async () => {
  const real = await vi.importActual<typeof import('../../api/reports')>('../../api/reports')
  return { ...real, ...api }
})

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toast }))

const REPORT_ID = 'a1b2c3d4e5f60718'

const OUTLINE: TemplateOutline = {
  mode: 'untagged',
  language: 'de',
  headings: [
    { index: 0, level: 0, text: 'Bericht', style: 'Title', is_body_start: false },
    { index: 5, level: 1, text: 'Zusammenfassung', style: 'Heading 1', is_body_start: true },
    { index: 9, level: 1, text: 'Lorem ipsum', style: 'Heading 1', is_body_start: false },
    { index: 12, level: 2, text: 'Kosten', style: 'Heading 2', is_body_start: false },
  ],
  tags: [],
  placeholders: [{ text: '[Client name]', paragraph_index: 2 }, { text: '<Date>', paragraph_index: 3 }],
  tables: [],
  header_text: '',
  footer_text: '',
  body_start_index: 5,
  n_paragraphs: 16,
  has_toc: true,
  unsupported: [],
}

const SECTIONS = [
  { id: 'summary', title: 'Executive summary' },
  { id: 'cost', title: 'Cost at target' },
  { id: 'frontier', title: 'Cost-vs-availability frontier' },
  { id: 'gates', title: 'Dynamics gates' },
]

const PLAN: MappingPlan = {
  entries: [
    { heading_index: 0, action: 'keep', new_text: null, section_ids: [] },
    { heading_index: 5, action: 'rename', new_text: 'Summary', section_ids: ['summary'] },
    { heading_index: 9, action: 'drop', new_text: null, section_ids: [] },
    { heading_index: 12, action: 'keep', new_text: null, section_ids: ['cost'] },
  ],
  inserted: [{ after_heading_index: 12, section_id: 'frontier', heading: 'Frontier' }],
  placeholders: { '[Client name]': 'ACME' },
  unmapped_sections: ['gates'],
  notes: ['dropped "Lorem ipsum" (placeholder text)'],
}

function state(plan: MappingPlan | null): ReportTemplateState {
  return { template_file_id: 'f1', mode: 'untagged', language: 'de', outline: OUTLINE, plan }
}

function job(over: Partial<ReportJobRecord> = {}): ReportJobRecord {
  return {
    status: 'running',
    report_id: REPORT_ID,
    version: null,
    mode: 'mapping',
    section: null,
    progress: { done: 0, total: 1, current: null },
    repairs: 0,
    prose_failures: [],
    error: null,
    started_at: Date.now() / 1000 - 3,
    finished_at: null,
    profile_id: 'anthropic-default',
    model: 'claude-sonnet',
    ...over,
  }
}

/** The viewer's shape: the template query + the shared job hook feed the editor. */
function Harness({ onExport }: { onExport: () => void }) {
  const template = useQuery({
    queryKey: REPORT_TEMPLATE_KEY('Demo', REPORT_ID),
    queryFn: () => api.getReportTemplate('Demo', REPORT_ID) as Promise<ReportTemplateState>,
    retry: false,
  })
  const jobApi = useReportJob('Demo')
  if (!template.data?.outline) return <p>loading</p>
  return (
    <MappingPlanEditor
      project="Demo"
      reportId={REPORT_ID}
      outline={template.data.outline}
      plan={template.data.plan}
      language={template.data.language}
      sections={SECTIONS}
      job={jobApi}
      onExport={onExport}
    />
  )
}

function renderEditor() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const onExport = vi.fn()
  render(<QueryClientProvider client={client}><Harness onExport={onExport} /></QueryClientProvider>)
  return { client, onExport }
}

function row(index: number): HTMLElement {
  return screen.getByTestId(`mapping-row-${index}`)
}

beforeEach(() => {
  api.getReportTemplate.mockReset().mockResolvedValue(state(PLAN))
  api.putMappingPlan.mockReset().mockImplementation(async (_p: string, _r: string, plan: MappingPlan) => plan)
  api.proposeMappingPlan.mockReset().mockResolvedValue({ status: 'running', report_id: REPORT_ID })
  api.getGenerateStatus.mockReset().mockResolvedValue(null)
  api.abortGenerate.mockReset().mockResolvedValue({ status: 'running', aborting: true })
  toast.success.mockReset()
  toast.error.mockReset()
})

afterEach(() => cleanup())

describe('MappingPlanEditor', () => {
  it('renders one row per template heading with index, level, text, style and the stored entry', async () => {
    renderEditor()
    const rows = await screen.findAllByTestId(/^mapping-row-/)
    expect(rows.map(r => r.dataset.headingIndex)).toEqual(['0', '5', '9', '12'])
    const r5 = within(row(5))
    expect(row(5).textContent).toContain('Zusammenfassung')
    expect(row(5).textContent).toContain('Heading 1')
    expect(row(5).textContent).toContain('H1')
    expect((r5.getByLabelText('Action for heading 5') as HTMLSelectElement).value).toBe('rename')
    expect((r5.getByLabelText('New text for heading 5') as HTMLInputElement).value).toBe('Summary')
    const multi = r5.getByLabelText('Sections for heading 5') as HTMLSelectElement
    expect(multi.multiple).toBe(true)
    expect(Array.from(multi.selectedOptions).map(o => o.value)).toEqual(['summary'])
    expect(Array.from(multi.options).map(o => o.textContent)).toEqual(SECTIONS.map(s => s.title))
    expect((within(row(9)).getByLabelText('Action for heading 9') as HTMLSelectElement).value).toBe('drop')
    expect(within(row(9)).queryByLabelText('New text for heading 9')).toBeNull()
    expect(within(row(12)).getByText('Kosten')).toBeTruthy()
    expect(row(12).textContent).toContain('H2')

    // read-only: unmapped sections by title, and the notes
    expect(screen.getByTestId('mapping-unmapped').textContent).toContain('Dynamics gates')
    expect(screen.getByTestId('mapping-notes').textContent).toContain('dropped "Lorem ipsum"')
    // placeholders: the outline's keys, with the stored value
    expect((screen.getByLabelText('Placeholder [Client name]') as HTMLInputElement).value).toBe('ACME')
    expect((screen.getByLabelText('Placeholder <Date>') as HTMLInputElement).value).toBe('')
    // clean → Save disabled, Export enabled
    expect((screen.getByTestId('mapping-save') as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByTestId('mapping-export') as HTMLButtonElement).disabled).toBe(false)
  })

  it('starts from a blank keep-everything plan when none is stored', async () => {
    api.getReportTemplate.mockResolvedValue(state(null))
    renderEditor()
    await screen.findAllByTestId(/^mapping-row-/)
    for (const i of [0, 5, 9, 12]) {
      expect((within(row(i)).getByLabelText(`Action for heading ${i}`) as HTMLSelectElement).value).toBe('keep')
      expect((within(row(i)).getByLabelText(`Sections for heading ${i}`) as HTMLSelectElement).selectedOptions).toHaveLength(0)
    }
    expect(screen.queryByTestId('mapping-notes')).toBeNull()
    expect(screen.queryByTestId('mapping-unmapped')).toBeNull()
  })

  it('row actions, the rename text and the multi-select mark the plan dirty and are PUT (non-strict) on Save, then re-read', async () => {
    const user = userEvent.setup()
    renderEditor()
    await screen.findAllByTestId(/^mapping-row-/)
    await user.selectOptions(within(row(12)).getByLabelText('Action for heading 12'), 'rename')
    const rename = within(row(12)).getByLabelText('New text for heading 12') as HTMLInputElement
    expect(rename.value).toBe('Kosten') // pre-filled with the template's heading
    await user.clear(rename)
    await user.type(rename, 'Costs')
    await user.selectOptions(within(row(12)).getByLabelText('Sections for heading 12'), ['cost', 'gates'])
    await user.selectOptions(within(row(9)).getByLabelText('Action for heading 9'), 'keep')
    expect(screen.getByTestId('mapping-dirty')).toBeTruthy()
    const save = screen.getByTestId('mapping-save') as HTMLButtonElement
    expect(save.disabled).toBe(false)
    expect((screen.getByTestId('mapping-export') as HTMLButtonElement).disabled).toBe(true)
    await user.click(save)
    await waitFor(() => expect(api.putMappingPlan).toHaveBeenCalledTimes(1))
    const [project, reportId, sent, opts] = api.putMappingPlan.mock.calls[0]
    expect(project).toBe('Demo')
    expect(reportId).toBe(REPORT_ID)
    expect(opts ?? {}).not.toHaveProperty('strict')
    expect(sent.entries).toEqual([
      { heading_index: 0, action: 'keep', new_text: null, section_ids: [] },
      { heading_index: 5, action: 'rename', new_text: 'Summary', section_ids: ['summary'] },
      { heading_index: 9, action: 'keep', new_text: null, section_ids: [] },
      { heading_index: 12, action: 'rename', new_text: 'Costs', section_ids: ['cost', 'gates'] },
    ])
    expect(sent.inserted).toEqual(PLAN.inserted)
    expect(sent.placeholders).toEqual({ '[Client name]': 'ACME' })
    await waitFor(() => expect(api.getReportTemplate).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.queryByTestId('mapping-dirty')).toBeNull())
    expect(toast.success).toHaveBeenCalled()
  })

  it('adds and removes inserted sections', async () => {
    const user = userEvent.setup()
    renderEditor()
    await screen.findAllByTestId(/^mapping-row-/)
    let inserted = screen.getAllByTestId('mapping-inserted-row')
    expect(inserted).toHaveLength(1)
    expect((within(inserted[0]).getByLabelText('Inserted section 0') as HTMLSelectElement).value).toBe('frontier')
    expect((within(inserted[0]).getByLabelText('Insert after heading 0') as HTMLSelectElement).value).toBe('12')
    expect((within(inserted[0]).getByLabelText('Inserted heading 0') as HTMLInputElement).value).toBe('Frontier')

    await user.click(screen.getByTestId('mapping-insert-add'))
    inserted = screen.getAllByTestId('mapping-inserted-row')
    expect(inserted).toHaveLength(2)
    // the new row defaults to the first unmapped section after the last heading
    expect((within(inserted[1]).getByLabelText('Inserted section 1') as HTMLSelectElement).value).toBe('gates')
    expect((within(inserted[1]).getByLabelText('Inserted heading 1') as HTMLInputElement).value).toBe('Dynamics gates')
    await user.selectOptions(within(inserted[1]).getByLabelText('Insert after heading 1'), '5')
    await user.click(within(inserted[0]).getByLabelText('Remove insertion 0'))
    inserted = screen.getAllByTestId('mapping-inserted-row')
    expect(inserted).toHaveLength(1)
    await user.click(screen.getByTestId('mapping-save'))
    await waitFor(() => expect(api.putMappingPlan).toHaveBeenCalled())
    expect(api.putMappingPlan.mock.calls[0][2].inserted).toEqual([
      { after_heading_index: 5, section_id: 'gates', heading: 'Dynamics gates' },
    ])
  })

  it('placeholder values are PUT with the plan (empty ones omitted)', async () => {
    const user = userEvent.setup()
    renderEditor()
    await screen.findAllByTestId(/^mapping-row-/)
    await user.type(screen.getByLabelText('Placeholder <Date>'), '2026-09-29')
    await user.click(screen.getByTestId('mapping-save'))
    await waitFor(() => expect(api.putMappingPlan).toHaveBeenCalled())
    expect(api.putMappingPlan.mock.calls[0][2].placeholders).toEqual({ '[Client name]': 'ACME', '<Date>': '2026-09-29' })
  })

  it('"Propose with the assistant" starts the mapping job, shows the strip, and the plan refreshes when it is done', async () => {
    api.getReportTemplate.mockResolvedValue(state(null))
    const user = userEvent.setup()
    renderEditor()
    await screen.findAllByTestId(/^mapping-row-/)
    api.getGenerateStatus
      .mockResolvedValueOnce(job())
      .mockResolvedValue(job({ status: 'done', progress: { done: 1, total: 1, current: null }, finished_at: Date.now() / 1000 }))
    await user.click(screen.getByTestId('mapping-propose'))
    await waitFor(() => expect(api.proposeMappingPlan).toHaveBeenCalledWith('Demo', REPORT_ID, { language: 'de' }))
    const strip = await screen.findByTestId('report-job-strip')
    expect(strip.textContent).toMatch(/proposing the mapping plan/i)
    expect((screen.getByTestId('mapping-propose') as HTMLButtonElement).disabled).toBe(true)
    // the backend stores the proposal by the time the record says done
    api.getReportTemplate.mockResolvedValue(state(PLAN))
    await waitFor(() => expect(screen.getByTestId('report-job-strip').dataset.status).toBe('done'), { timeout: 4000 })
    await waitFor(() => expect(api.getReportTemplate).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(
      (within(row(5)).getByLabelText('Action for heading 5') as HTMLSelectElement).value,
    ).toBe('rename'))
    expect(screen.getByTestId('mapping-notes').textContent).toContain('Lorem ipsum')
    expect((screen.getByTestId('mapping-propose') as HTMLButtonElement).disabled).toBe(false)
  })

  it('toasts the template copy when a proposal is refused', async () => {
    const { ReportsError } = await vi.importActual<typeof import('../../api/reports')>('../../api/reports')
    api.proposeMappingPlan.mockRejectedValue(new ReportsError(
      { error_kind: 'template_not_untagged', message: 'tagged' }, 400,
    ))
    const user = userEvent.setup()
    renderEditor()
    await screen.findAllByTestId(/^mapping-row-/)
    await user.click(screen.getByTestId('mapping-propose'))
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(String(toast.error.mock.calls[0][0])).toMatch(/tagged template/i)
  })

  it('toasts a refused plan (invalid_mapping_plan) with the backend notes and keeps the draft dirty', async () => {
    const { ReportsError } = await vi.importActual<typeof import('../../api/reports')>('../../api/reports')
    api.putMappingPlan.mockRejectedValue(new ReportsError(
      { error_kind: 'invalid_mapping_plan', message: 'heading 99 is out of range; section "nope" unknown' }, 400,
    ))
    const user = userEvent.setup()
    renderEditor()
    await screen.findAllByTestId(/^mapping-row-/)
    await user.selectOptions(within(row(0)).getByLabelText('Action for heading 0'), 'drop')
    await user.click(screen.getByTestId('mapping-save'))
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(String(toast.error.mock.calls[0][0])).toMatch(/heading 99 is out of range/)
    expect(screen.getByTestId('mapping-dirty')).toBeTruthy()
    expect((within(row(0)).getByLabelText('Action for heading 0') as HTMLSelectElement).value).toBe('drop')
  })

  it('"Export with this template" calls the viewer\'s export path', async () => {
    const user = userEvent.setup()
    const { onExport } = renderEditor()
    await screen.findAllByTestId(/^mapping-row-/)
    await user.click(screen.getByTestId('mapping-export'))
    expect(onExport).toHaveBeenCalledTimes(1)
  })

  it('abort on the strip posts the abort route', async () => {
    api.getGenerateStatus.mockResolvedValue(job())
    const user = userEvent.setup()
    renderEditor()
    const strip = await screen.findByTestId('report-job-strip')
    await user.click(within(strip).getByTestId('report-job-abort'))
    await waitFor(() => expect(api.abortGenerate).toHaveBeenCalledWith('Demo'))
    await act(async () => {})
  })
})
