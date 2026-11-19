// SH-07d PR B: the run card's report revision drafts (GK B (68) R2, R3, R5,
// R6; UX (26) conditions 3, 5, 6).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import RevisionDrafts, { DRAFT_POLL_MS, anchorWords } from './RevisionDrafts'
import * as reports from '../../api/reports'
import { ReportsError, type ReportDocument, type RevisionDraft } from '../../api/reports'
import type { RunMonitorView } from '../../api/runMonitor'
import { useUIStore } from '../../store/uiStore'
import { STATES, TASK_ID } from './runFixtures'
import { DRAFT_REFUSAL_WORDS, DRAFT_STATUS_WORDS } from './runWords'

vi.mock('../../api/reports', async (orig) => {
  const real = await orig<typeof import('../../api/reports')>()
  return {
    ...real, listReportComments: vi.fn(), getReport: vi.fn(), requestRevisionDraft: vi.fn(),
    confirmRevisionDraft: vi.fn(), discardRevisionDraft: vi.fn(),
  }
})

const RID = 'r-1'
const OFFER = { report_id: RID, max_requests: 10, in_flight: ['requested', 'drafting'], may_act: true }
const COMMENT = {
  comment_id: 'c1', text: 'Make it shorter', anchor: { section_id: 'grid', block_index: 1 },
  status: 'open' as const, created_at: '2026-11-19T08:00:00Z',
}
const DOC = {
  report_id: RID, sections: [{ section_id: 'grid', heading: 'Grid connection', blocks: [
    { type: 'bullets', items: ['a'] }, { type: 'paragraph', md: 'Long text' },
    { type: 'table_ref', table_id: 't1', caption: 'Tariffs' }] }],
} as unknown as ReportDocument

const row = (over: Partial<RevisionDraft> = {}): RevisionDraft => ({
  id: 'd1', comment_id: 'c1', report_id: RID, status: 'drafted',
  edit: { kind: 'paragraph_text', md: 'Short text' }, base_digest: 'x', ...over,
})

function viewWith(monitor: Partial<RunMonitorView['monitor']>): RunMonitorView {
  const base = STATES.finished_report
  return { ...base, monitor: { ...base.monitor, ...monitor } }
}

function show(monitor: Partial<RunMonitorView['monitor']>, onRefresh = vi.fn(async () => undefined)) {
  const utils = render(<RevisionDrafts taskId={TASK_ID} view={viewWith(monitor)} onRefresh={onRefresh} />)
  return { ...utils, onRefresh }
}

beforeEach(() => {
  cleanup()
  vi.clearAllMocks()
  useUIStore.setState({ currentProject: 'Project A' })
  vi.mocked(reports.listReportComments).mockResolvedValue({ report_id: RID, latest_version: 1, digest: 'x', comments: [COMMENT] })
  vi.mocked(reports.getReport).mockResolvedValue(DOC)
})
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('RevisionDrafts', () => {
  it('shows nothing on a run with no offer and no drafts', () => {
    const { container } = show({})
    expect(container.textContent).toBe('')
    expect(reports.listReportComments).not.toHaveBeenCalled()
  })

  it("lists the report's open comments with where they sit, and asks the named run to draft", async () => {
    const { onRefresh } = show({ revision_report: OFFER })
    expect(await screen.findByText('Grid connection, paragraph 1')).toBeTruthy()
    expect(reports.listReportComments).toHaveBeenCalledWith('Project A', RID)
    vi.mocked(reports.requestRevisionDraft).mockResolvedValue({ draft: row({ status: 'requested' }) })
    fireEvent.click(screen.getByTestId('revision-draft-request'))
    await waitFor(() => expect(reports.requestRevisionDraft).toHaveBeenCalledWith('Project A', RID, 'c1', TASK_ID))
    await waitFor(() => expect(onRefresh).toHaveBeenCalled())
  })

  it('names a table and a bullet list by their kind, and never a raw id', () => {
    expect(anchorWords({ section_id: 'grid', table_id: 't1' }, DOC)).toBe('Grid connection, table "Tariffs"')
    expect(anchorWords({ section_id: 'grid', block_index: 0 }, DOC)).toBe('Grid connection, bullet list')
    expect(anchorWords({ section_id: 'gone', block_index: 0 }, DOC)).toBe('A part of the report')
    expect(anchorWords({ section_id: 'grid', block_index: 0 }, null)).toBe('A part of the report')
  })

  it('disables Draft a fix while a draft is in flight, and says why in a status', async () => {
    show({ revision_report: OFFER, revision_drafts: [row({ status: 'drafting', edit: undefined })] })
    const button = await screen.findByTestId('revision-draft-request')
    expect((button as HTMLButtonElement).disabled).toBe(true)
    const why = screen.getByTestId('revision-blocked')
    expect(why.getAttribute('role')).toBe('status')
    expect(why.textContent).toMatch(/being made/)
  })

  it("disables Draft a fix at the server's limit, not a constant of its own (R2)", async () => {
    const rows = [1, 2, 3].map(i => row({ id: `d${i}`, status: 'discarded' }))
    show({ revision_report: { ...OFFER, max_requests: 3 }, revision_drafts: rows })
    expect(((await screen.findByTestId('revision-draft-request')) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByTestId('revision-blocked').textContent).toBe('This run has made its 3 drafts.')
    cleanup()
    show({ revision_report: { ...OFFER, max_requests: 4 }, revision_drafts: rows })
    expect(((await screen.findByTestId('revision-draft-request')) as HTMLButtonElement).disabled).toBe(false)
  })

  it('shows a 409 as its words and lets the button work again (R2, R6)', async () => {
    vi.mocked(reports.requestRevisionDraft).mockRejectedValue(
      new ReportsError({ error_kind: 'revision_draft_limit', message: 'This run is already drafting; wait for that draft first.' }, 409))
    show({ revision_report: OFFER })
    fireEvent.click(await screen.findByTestId('revision-draft-request'))
    const refusal = await screen.findByTestId('revision-refusal')
    expect(refusal.textContent).toBe('This run is already drafting; wait for that draft first.')
    expect(refusal.getAttribute('role')).toBe('status')
    await waitFor(() => expect((screen.getByTestId('revision-draft-request') as HTMLButtonElement).disabled).toBe(false))
  })

  it('maps a known refusal kind to its words, and an unknown one to the server message, never blank (R6)', async () => {
    vi.mocked(reports.confirmRevisionDraft).mockRejectedValueOnce(
      new ReportsError({ error_kind: 'revision_stale_base', message: 'raw server text' }, 409))
    show({ revision_report: OFFER, revision_drafts: [row()] })
    fireEvent.click(await screen.findByTestId('revision-draft-confirm'))
    expect((await screen.findByTestId('revision-refusal')).textContent).toBe(DRAFT_REFUSAL_WORDS.revision_stale_base)
    vi.mocked(reports.confirmRevisionDraft).mockRejectedValueOnce(
      new ReportsError({ error_kind: 'something_new', message: 'The server says why.' }, 409))
    fireEvent.click(screen.getByTestId('revision-draft-confirm'))
    await waitFor(() => expect(screen.getByTestId('revision-refusal').textContent).toBe('The server says why.'))
    vi.mocked(reports.confirmRevisionDraft).mockRejectedValueOnce(
      new ReportsError({ error_kind: 'something_new', message: '' }, 500))
    fireEvent.click(screen.getByTestId('revision-draft-confirm'))
    await waitFor(() => expect(screen.getByTestId('revision-refusal').textContent).toBe('That did not go through: try again.'))
  })

  it('confirms and discards a drafted row through the routes, with the run named', async () => {
    vi.mocked(reports.confirmRevisionDraft).mockResolvedValue({})
    vi.mocked(reports.discardRevisionDraft).mockResolvedValue({ draft: row({ status: 'discarded' }) })
    show({ revision_report: OFFER, revision_drafts: [row()] })
    fireEvent.click(await screen.findByTestId('revision-draft-confirm'))
    await waitFor(() => expect(reports.confirmRevisionDraft).toHaveBeenCalledWith('Project A', RID, 'd1', TASK_ID))
    await waitFor(() => expect((screen.getByTestId('revision-draft-discard') as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(screen.getByTestId('revision-draft-discard'))
    await waitFor(() => expect(reports.discardRevisionDraft).toHaveBeenCalledWith('Project A', RID, 'd1', TASK_ID))
  })

  it('offers Confirm and Discard on a drafted row only', async () => {
    const statuses = Object.keys(DRAFT_STATUS_WORDS)
    show({ revision_report: OFFER, revision_drafts: statuses.map((s, i) => row({ id: `d${i}`, status: s })) })
    await screen.findAllByTestId('revision-draft')
    expect(screen.getAllByTestId('revision-draft-confirm')).toHaveLength(1)
    expect(screen.getAllByTestId('revision-draft-discard')).toHaveLength(1)
  })

  it('shows every status in plain words, never the engine word or a raw code (UX 3)', async () => {
    const statuses = Object.keys(DRAFT_STATUS_WORDS)
    show({ revision_report: OFFER, revision_drafts: [
      ...statuses.map((s, i) => row({ id: `d${i}`, status: s, error: s === 'refused' ? 'provider_error' : undefined })),
      row({ id: 'r1', status: 'rejected', edit: undefined, reasons: ['draft_truncated', 'changes_number'] }),
      row({ id: 'r2', status: 'refused', error: 'revision_stale_base' }),
    ] })
    const list = await screen.findByTestId('revision-draft-list')
    const text = list.textContent ?? ''
    for (const raw of ['requested', 'drafting', 'drafted', 'rejected', 'refused', 'applying', 'discarded',
      'provider_error', 'draft_truncated', 'changes_number', 'revision_stale_base']) {
      expect(text).not.toMatch(new RegExp(`\\b${raw}\\b`))
    }
    expect(text).toMatch(/cut off/)
    expect(text).toMatch(/Not applied/)
    for (const s of screen.getAllByTestId('revision-draft-status')) expect(s.getAttribute('role')).toBe('status')
  })

  it('renders comment, edit and cell text as text, never as markup (R3)', async () => {
    const evil = '<img src=x onerror="window.__pwned=1">'
    vi.mocked(reports.listReportComments).mockResolvedValue({ report_id: RID, latest_version: 1, digest: 'x',
      comments: [{ ...COMMENT, text: evil }] })
    const { container } = show({ revision_report: OFFER, revision_drafts: [
      row({ edit: { kind: 'paragraph_text', md: evil } }),
      row({ id: 'd2', edit: { kind: 'table_cells', rows: [[evil, '**bold**']] } }),
    ] })
    await screen.findAllByTestId('draft-edit')
    await waitFor(() => expect(container.textContent).toContain(`For: ${evil}`))
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('strong')).toBeNull()
    expect(container.querySelector('a')).toBeNull()
    expect(screen.getAllByText(evil).length).toBeGreaterThanOrEqual(3)
  })

  it('gives a viewer no Draft a fix, Confirm or Discard, and no disabled stub (UX 6)', async () => {
    show({ revision_report: { ...OFFER, may_act: false }, revision_drafts: [row()] })
    expect(await screen.findByText('Make it shorter')).toBeTruthy()
    expect(screen.queryByTestId('revision-draft-request')).toBeNull()
    expect(screen.queryByTestId('revision-draft-confirm')).toBeNull()
    expect(screen.queryByTestId('revision-draft-discard')).toBeNull()
    expect(screen.queryByTestId('revision-blocked')).toBeNull()
  })

  it('polls only while a draft is in flight, one interval, stopped on unmount and a project switch (R5)', async () => {
    vi.useFakeTimers()
    const onRefresh = vi.fn(async () => undefined)
    const settled = render(<RevisionDrafts taskId={TASK_ID} view={viewWith({ revision_report: OFFER, revision_drafts: [row()] })} onRefresh={onRefresh} />)
    await act(async () => { vi.advanceTimersByTime(DRAFT_POLL_MS * 3) })
    expect(onRefresh).not.toHaveBeenCalled()
    settled.unmount()

    const flying = viewWith({ revision_report: OFFER, revision_drafts: [row({ status: 'requested', edit: undefined })] })
    const { rerender, unmount } = render(<RevisionDrafts taskId={TASK_ID} view={flying} onRefresh={onRefresh} />)
    rerender(<RevisionDrafts taskId={TASK_ID} view={{ ...flying }} onRefresh={onRefresh} />)
    await act(async () => { vi.advanceTimersByTime(DRAFT_POLL_MS) })
    expect(onRefresh).toHaveBeenCalledTimes(1)
    await act(async () => { useUIStore.setState({ currentProject: 'Project B' }) })
    await act(async () => { vi.advanceTimersByTime(DRAFT_POLL_MS * 3) })
    expect(onRefresh).toHaveBeenCalledTimes(1)
    await act(async () => { useUIStore.setState({ currentProject: 'Project A' }) })
    await act(async () => { vi.advanceTimersByTime(DRAFT_POLL_MS) })
    expect(onRefresh).toHaveBeenCalledTimes(2)
    unmount()
    await act(async () => { vi.advanceTimersByTime(DRAFT_POLL_MS * 3) })
    expect(onRefresh).toHaveBeenCalledTimes(2)
  })
})
