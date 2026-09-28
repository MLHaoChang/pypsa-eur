/**
 * The report viewer (WP7a) renders a `ReportDocument` exactly as the backend
 * stored it: sections in order, prose through ChatMarkdown, tables and
 * figures from the document's own `tables` / `figures` maps, callouts as
 * labelled boxes, a not-established section stating its note, and every
 * number the audit could not find in the evidence wrapped in a `<mark>`.
 * Export posts to the export route and hands the blob URL to an anchor.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReportDocument, ReportMeta } from '../../api/reports'
import { ReportViewer } from './ReportViewer'

const api = vi.hoisted(() => ({
  getReport: vi.fn(),
  exportReport: vi.fn(),
}))

vi.mock('../../api/reports', async () => {
  const real = await vi.importActual<typeof import('../../api/reports')>('../../api/reports')
  return { ...real, getReport: api.getReport, exportReport: api.exportReport }
})

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toast }))

const REPORT_ID = 'a1b2c3d4e5f60718'

const META: ReportMeta = {
  report_id: REPORT_ID,
  title: 'Study report — Demo',
  created_at: '2026-09-28T10:00:00+00:00',
  updated_at: '2026-09-28T10:00:00+00:00',
  latest_version: 1,
  mode: 'evidence_only',
  evidence_hash: 'deadbeef',
  profile_id: null,
  model: null,
}

export const FIXTURE: ReportDocument = {
  schema_version: 1,
  report_id: REPORT_ID,
  version: 1,
  title: 'Study report — Demo',
  language: 'en',
  created_at: '2026-09-28T10:00:00+00:00',
  evidence_hash: 'deadbeef',
  profile_id: null,
  model: null,
  mode: 'evidence_only',
  template_file_id: null,
  sections: [
    {
      section_id: 'summary',
      heading: 'Executive summary',
      source: 'llm',
      status: 'ok',
      blocks: [
        { type: 'paragraph', md: 'LOLE is **2.4** h/yr and the cost at target is 1,250 EUR.' },
        { type: 'bullets', items: ['First point with 3.1 %', 'Second point'] },
      ],
      note: null,
      audit: { unverified: ['2.4'], verified: [{ text: '1,250', path: '/cost' }] },
    },
    {
      section_id: 'frontier',
      heading: 'Cost–reliability frontier',
      source: 'code',
      status: 'ok',
      blocks: [
        { type: 'table_ref', table_id: 'frontier', caption: 'Frontier points' },
        { type: 'figure_ref', figure_id: 'frontier', caption: 'Frontier plot' },
        { type: 'table_ref', table_id: 't_missing', caption: null },
      ],
      note: null,
      audit: { unverified: [], verified: [] },
    },
    {
      section_id: 'gates',
      heading: 'Certification gates',
      source: 'code',
      status: 'not_established',
      blocks: [
        { type: 'callout', kind: 'not_established', text: 'Gates: not established.' },
        { type: 'callout', kind: 'disclosure', text: 'Costs exclude shed cost.' },
        { type: 'callout', kind: 'gap', text: 'No MC certification in this session.' },
        { type: 'field', key: 'Archetype', value: 'strong_grid' },
      ],
      note: 'No MC certification ran in this session.',
      audit: { unverified: [], verified: [] },
    },
  ],
  tables: {
    frontier: {
      table_id: 'frontier',
      columns: ['Point', 'Cost (EUR)', 'ENS (‱)'],
      rows: [['A', '1,000', '12.0'], ['B', '1,250', '8.5']],
      caption: 'Frontier points',
      source_path: '/frontier/points',
    },
  },
  figures: {
    frontier: { figure_id: 'frontier', png_file: 'frontier.png', caption: 'Frontier plot', source_path: null },
  },
}

function renderViewer(meta: ReportMeta = META) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const onBack = vi.fn()
  render(
    <QueryClientProvider client={client}>
      <ReportViewer project="Demo" meta={meta} onBack={onBack} />
    </QueryClientProvider>,
  )
  return { onBack }
}

beforeEach(() => {
  api.getReport.mockReset().mockResolvedValue(FIXTURE)
  api.exportReport.mockReset()
  toast.success.mockReset()
  toast.error.mockReset()
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('ReportViewer', () => {
  it('renders every section in order with every block type', async () => {
    renderViewer()
    const sections = await screen.findAllByTestId('report-section')
    expect(sections.map(s => s.dataset.sectionId)).toEqual(['summary', 'frontier', 'gates'])

    // paragraph + bullets through ChatMarkdown (the bold survives)
    const summary = within(sections[0])
    expect(summary.getByText('2.4').tagName).toBe('MARK')
    expect(summary.getByText('Second point').tagName).toBe('LI')
    expect(summary.getByText('2.4').closest('strong')).not.toBeNull()

    // table_ref → a real table from doc.tables with its caption
    const table = within(sections[1]).getByRole('table')
    expect(within(table).getByText('Frontier points').tagName).toBe('CAPTION')
    expect(within(table).getAllByRole('columnheader').map(h => h.textContent))
      .toEqual(['Point', 'Cost (EUR)', 'ENS (‱)'])
    expect(within(table).getByText('1,250')).toBeTruthy()

    // figure_ref → <img> from the figure route with its caption
    const img = within(sections[1]).getByRole('img', { name: 'Frontier plot' }) as HTMLImageElement
    expect(img.getAttribute('src')).toBe(`/api/projects/Demo/reports/${REPORT_ID}/figures/frontier`)
    expect(within(sections[1]).getByText('Frontier plot', { selector: 'figcaption' })).toBeTruthy()

    // callouts: label + icon, never colour alone
    const gates = within(sections[2])
    expect(gates.getByTestId('callout-not_established').textContent).toContain('Not established')
    expect(gates.getByTestId('callout-disclosure').textContent).toContain('Disclosure')
    expect(gates.getByTestId('callout-gap').textContent).toContain('Evidence gap')
    expect(gates.getByText('Costs exclude shed cost.')).toBeTruthy()

    // field → label/value row
    expect(gates.getByText('Archetype')).toBeTruthy()
    expect(gates.getByText('strong_grid')).toBeTruthy()
  })

  it('marks unverified numbers with the evidence tooltip and leaves verified ones alone', async () => {
    renderViewer()
    const mark = await screen.findByText('2.4')
    expect(mark.tagName).toBe('MARK')
    expect(mark.getAttribute('title')).toBe('not found in the evidence')
    const sections = screen.getAllByTestId('report-section')
    expect(within(sections[0]).getByText(/1,250 EUR/).tagName).not.toBe('MARK')
    expect(within(sections[0]).queryByText('3.1')).toBeNull() // bullets are not split
  })

  it('shows the note of a not_established section, tagged as such', async () => {
    renderViewer()
    const sections = await screen.findAllByTestId('report-section')
    const gates = within(sections[2])
    expect(gates.getByTestId('section-status').textContent).toMatch(/not established/i)
    expect(gates.getByTestId('section-note').textContent).toBe('No MC certification ran in this session.')
    expect(within(sections[0]).queryByTestId('section-note')).toBeNull()
  })

  it('states a missing table id inline instead of rendering nothing', async () => {
    renderViewer()
    const sections = await screen.findAllByTestId('report-section')
    expect(within(sections[1]).getByTestId('table-missing').textContent)
      .toContain('t_missing')
  })

  it('exports the .docx through the API and hands the blob URL to an anchor download', async () => {
    api.exportReport.mockResolvedValueOnce({
      file_id: 'f1', filename: 'report_v1.docx', kind: 'agent_export', mime: 'application/x',
    })
    const clicked: HTMLAnchorElement[] = []
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(this)
    })
    const user = userEvent.setup()
    renderViewer()
    await screen.findAllByTestId('report-section')
    await user.click(screen.getByTestId('report-export'))
    await waitFor(() => expect(api.exportReport).toHaveBeenCalledWith('Demo', REPORT_ID, { version: 1 }))
    await waitFor(() => expect(clicked).toHaveLength(1))
    expect(clicked[0].getAttribute('href')).toBe('/api/projects/Demo/uploads/f1/blob')
    expect(clicked[0].getAttribute('download')).toBe('report_v1.docx')
    expect(toast.success).toHaveBeenCalled()
  })

  it('surfaces an export refusal as a toast with the backend message', async () => {
    const { ReportsError } = await vi.importActual<typeof import('../../api/reports')>('../../api/reports')
    api.exportReport.mockRejectedValueOnce(new ReportsError(
      { error_kind: 'project_locked', message: "'Demo' is being edited by another user." }, 409,
    ))
    const user = userEvent.setup()
    renderViewer()
    await screen.findAllByTestId('report-section')
    await user.click(screen.getByTestId('report-export'))
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(String(toast.error.mock.calls[0][0])).toContain('being edited by another user')
  })

  it('offers a version switcher only when there is more than one version, and refetches on change', async () => {
    renderViewer()
    await screen.findAllByTestId('report-section')
    expect(screen.queryByLabelText('Version')).toBeNull()
    cleanup()

    api.getReport.mockResolvedValue({ ...FIXTURE, version: 3 })
    const user = userEvent.setup()
    renderViewer({ ...META, latest_version: 3 })
    const select = await screen.findByLabelText('Version') as HTMLSelectElement
    expect(Array.from(select.options).map(o => o.value)).toEqual(['3', '2', '1'])
    await user.selectOptions(select, '2')
    await waitFor(() => expect(api.getReport).toHaveBeenLastCalledWith('Demo', REPORT_ID, 2))
  })

  it('shows a report_not_found refusal as text rather than a blank page', async () => {
    const { ReportsError } = await vi.importActual<typeof import('../../api/reports')>('../../api/reports')
    api.getReport.mockRejectedValue(new ReportsError(
      { error_kind: 'report_not_found', message: 'No such report (or version) in this project.' }, 404,
    ))
    renderViewer()
    expect((await screen.findByTestId('report-error')).textContent).toContain('No such report')
  })

  it('has a Back control that calls onBack', async () => {
    const user = userEvent.setup()
    const { onBack } = renderViewer()
    await screen.findAllByTestId('report-section')
    await user.click(screen.getByTestId('report-back'))
    expect(onBack).toHaveBeenCalledTimes(1)
  })
})
