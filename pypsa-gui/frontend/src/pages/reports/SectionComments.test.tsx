/**
 * 07g: a section's report comments in the viewer. The POST carries the
 * digest the viewer read (GK B condition 3), withdraw is offered only on the
 * signed-in user's own open comments behind an inline confirm, closed rows
 * have no action, reasons and refusals read in words, and the list folds.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReportComment, ReportDocument, Section } from '../../api/reports'
import { DRAFT_REASON_WORDS } from '../../components/runMonitor/runWords'
import { SectionCard } from './SectionCard'
import { COMMENT_REFUSAL_WORDS, MAX_COMMENT_CHARS } from './commentWords'
import { commentableParts, type CommentingProps } from './SectionComments'

const SECTION: Section = {
  section_id: 'grid',
  heading: 'Grid',
  source: 'llm',
  status: 'ok',
  blocks: [
    { type: 'paragraph', md: 'The grid **binds** in winter.' },
    { type: 'callout', kind: 'disclosure', text: 'Costs exclude shed cost.' },
    { type: 'bullets', items: ['Line A', 'Line B'] },
    { type: 'table_ref', table_id: 'caps', caption: null },
  ],
  note: null,
  audit: { unverified: [], verified: [] },
}

const DOC: ReportDocument = {
  schema_version: 1, report_id: 'r1', version: 2, title: 'T', language: 'en',
  created_at: '2026-11-28T10:00:00+00:00', evidence_hash: 'e', profile_id: null, model: null,
  mode: 'generated', template_file_id: null, sections: [SECTION],
  tables: { caps: { table_id: 'caps', columns: ['A'], rows: [['1']], caption: 'Capacity by carrier', source_path: null } },
  figures: {},
}

function comment(over: Partial<ReportComment> = {}): ReportComment {
  return {
    comment_id: 'c1', text: 'Shorter.', anchor: { section_id: 'grid', block_index: 0 },
    status: 'open', reason: null, reasons: [], created_at: '2026-11-27T09:00:00+00:00',
    created_by: 'me@example.com', ...over,
  }
}

function renderCard(over: Partial<CommentingProps> = {}) {
  const props: CommentingProps = {
    comments: [], project: 'Demo', reportId: 'r1', digest: 'd'.repeat(64), myEmail: 'me@example.com',
    canComment: true, onChanged: vi.fn(), onReload: vi.fn(), ...over,
  }
  render(<SectionCard section={SECTION} doc={DOC} project="Demo" commenting={props} />)
  return props
}

const ok = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
const refused = (status: number, kind: string) =>
  new Response(JSON.stringify({ detail: { error_kind: kind, message: 'm' } }), { status, headers: { 'Content-Type': 'application/json' } })

let fetchMock: ReturnType<typeof vi.fn<typeof fetch>>
beforeEach(() => { fetchMock = vi.fn<typeof fetch>(); vi.stubGlobal('fetch', fetchMock) })
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('commentableParts', () => {
  it('lists paragraphs, bullet lists and tables only, with their labels and anchors', () => {
    expect(commentableParts(SECTION, DOC).map(p => [p.label, p.anchor])).toEqual([
      ['Paragraph 1 · The grid binds in winter.', { section_id: 'grid', block_index: 0 }],
      ['Bullet list 1 · Line A', { section_id: 'grid', block_index: 2 }],
      ['Table · Capacity by carrier', { section_id: 'grid', table_id: 'caps' }],
    ])
  })
})

describe('SectionComments — create', () => {
  it('posts the text, the chosen anchor and the base digest it read', async () => {
    fetchMock.mockResolvedValue(ok(comment()))
    const user = userEvent.setup()
    const props = renderCard()
    await user.click(screen.getByTestId('section-comment'))
    await user.selectOptions(screen.getByTestId('comment-anchor'), 'tcaps')
    await user.type(screen.getByTestId('comment-text'), '  Round the totals.  ')
    await user.click(screen.getByTestId('comment-submit'))
    await waitFor(() => expect(props.onChanged).toHaveBeenCalledWith('Comment posted'))
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/projects/Demo/reports/r1/comments')
    expect(JSON.parse(String(init?.body))).toEqual({
      text: 'Round the totals.', anchor: { section_id: 'grid', table_id: 'caps' }, base_digest: 'd'.repeat(64),
    })
    expect(screen.queryByTestId('comment-form')).toBeNull()
  })

  it('offers no Comment… button off the latest version', () => {
    renderCard({ canComment: false })
    expect(screen.queryByTestId('section-comment')).toBeNull()
  })

  it.each([
    ['revision_stale_base', 409, true],
    ['report_comment_anchor_invalid', 422, true],
    ['report_job_in_flight', 409, false],
  ])('shows %s in words and keeps the text', async (kind, status, reload) => {
    fetchMock.mockResolvedValue(refused(status, kind))
    const user = userEvent.setup()
    const props = renderCard()
    await user.click(screen.getByTestId('section-comment'))
    await user.type(screen.getByTestId('comment-text'), 'Keep me')
    await user.click(screen.getByTestId('comment-submit'))
    const line = await screen.findByTestId('comment-refusal')
    expect(line.getAttribute('role')).toBe('status')
    expect((screen.getByTestId('comment-text') as HTMLTextAreaElement).value).toBe('Keep me')
    if (reload) {
      await user.click(within(line).getByTestId('comment-reload'))
      expect(props.onReload).toHaveBeenCalled()
    } else {
      expect(within(line).queryByTestId('comment-reload')).toBeNull()
    }
  })

  it('shows the counter near the cap and refuses an over-long comment without posting', async () => {
    const user = userEvent.setup()
    renderCard()
    await user.click(screen.getByTestId('section-comment'))
    const box = screen.getByTestId('comment-text')
    await user.click(box)
    await user.paste('x'.repeat(MAX_COMMENT_CHARS + 1))
    expect(screen.getByTestId('comment-counter').textContent).toBe('2,001 / 2000')
    await user.click(screen.getByTestId('comment-submit'))
    expect(screen.getByTestId('comment-refusal').textContent).toBe(COMMENT_REFUSAL_WORDS.invalid_comment)
    expect(fetchMock).not.toHaveBeenCalled()
  })
})

describe('SectionComments — list and withdraw', () => {
  it('orders open, addressed, withdrawn; shows reason words; folds after five', async () => {
    const user = userEvent.setup()
    renderCard({ comments: [
      comment({ comment_id: 'w', status: 'withdrawn' }),
      comment({ comment_id: 'a', status: 'addressed', revision: { version: 3, digest: 'x' } }),
      comment({ comment_id: 'o', anchor: { section_id: 'grid', block_index: 1 }, reason: 'outside_edit_set', reasons: ['outside_edit_set'] }),
      ...['1', '2', '3'].map(id => comment({ comment_id: id, status: 'withdrawn', created_by: 'other@example.com' })),
    ] })
    const rows = screen.getAllByTestId('report-comment')
    expect(rows.map(r => r.getAttribute('data-status'))).toEqual(['open', 'addressed', 'withdrawn', 'withdrawn', 'withdrawn'])
    expect(rows[0].textContent).toContain('Part 2')
    expect(within(rows[0]).getByTestId('comment-reason').textContent).toBe(DRAFT_REASON_WORDS.outside_edit_set)
    expect(rows[1].textContent).toContain('Addressed in v3')
    expect(within(rows[1]).queryByTestId('comment-withdraw')).toBeNull()
    expect(within(rows[2]).queryByTestId('comment-withdraw')).toBeNull()
    await user.click(screen.getByTestId('comments-show-all'))
    expect(screen.getAllByTestId('report-comment')).toHaveLength(6)
  })

  it('offers Withdraw only on my own open comments', () => {
    renderCard({ comments: [comment(), comment({ comment_id: 'c2', created_by: 'other@example.com' })] })
    const rows = screen.getAllByTestId('report-comment')
    expect(within(rows[0]).getByTestId('comment-withdraw')).toBeTruthy()
    expect(within(rows[1]).queryByTestId('comment-withdraw')).toBeNull()
  })

  it('withdraws only after the inline confirm', async () => {
    fetchMock.mockResolvedValue(ok(comment({ status: 'withdrawn' })))
    const user = userEvent.setup()
    const props = renderCard({ comments: [comment()] })
    await user.click(screen.getByTestId('comment-withdraw'))
    expect(screen.getByTestId('comment-withdraw-confirm').textContent).toContain('It cannot be reopened.')
    expect(fetchMock).not.toHaveBeenCalled()
    await user.click(screen.getByTestId('comment-withdraw-yes'))
    await waitFor(() => expect(props.onChanged).toHaveBeenCalledWith('Comment withdrawn'))
    expect(fetchMock.mock.calls[0][0]).toBe('/api/projects/Demo/reports/r1/comments/c1/withdraw')
  })

  it.each([
    ['report_comment_closed', 409],
    ['project_view_only', 403],
  ])('shows a refused withdraw (%s) in words', async (kind, status) => {
    fetchMock.mockResolvedValue(refused(status, kind))
    const user = userEvent.setup()
    renderCard({ comments: [comment()] })
    await user.click(screen.getByTestId('comment-withdraw'))
    await user.click(screen.getByTestId('comment-withdraw-yes'))
    expect((await screen.findByTestId('comment-row-refusal')).textContent).toBe(COMMENT_REFUSAL_WORDS[kind])
  })
})

describe('SectionComments — labels (UX 35)', () => {
  it('names a table without a caption by its order, never its id', () => {
    const doc = { ...DOC, tables: { ...DOC.tables, caps: { ...DOC.tables.caps, caption: null } } }
    expect(commentableParts(SECTION, doc).at(-1)!.label).toBe('Table 1')
  })

  it('names my own comments "You"', () => {
    renderCard({ comments: [comment(), comment({ comment_id: 'c2', created_by: 'other@example.com' })] })
    const rows = screen.getAllByTestId('report-comment')
    expect(rows[0].textContent).toContain('You · 2026-11-27')
    expect(rows[1].textContent).toContain('other@example.com · 2026-11-27')
  })
})
