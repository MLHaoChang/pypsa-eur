// Study harness 07g: a section's report comments (07c) in the report viewer.
//
// One list per section, under its header (UX 34 plan verdict: block indices
// of an older version may not line up with the shown one, so comments are
// not placed beside blocks). The "Comment…" button in the section header
// opens the form here; it is offered only on the latest version and only on
// a section that has a part a revision can edit (a paragraph, a bullet list
// or a table). Each comment is posted with the digest the viewer read
// (`base_digest`), so a newer version refuses it `revision_stale_base` and
// the typed text survives the reload. Withdraw is offered on the signed-in
// user's own open comments, behind an inline confirm (it is terminal).
import { useId, useState, type FormEvent } from 'react'
import type { Block, CommentAnchor, ReportComment, ReportDocument, Section } from '../../api/reports'
import { addReportComment, isReportError, withdrawReportComment } from '../../api/reports'
import { Tag } from '../../components/PageKit'
import { DRAFT_REASON_WORDS } from '../../components/runMonitor/runWords'
import { MAX_COMMENT_CHARS, commentRefusalWords } from './commentWords'

/** Rows shown before the list folds (#1332). */
export const COMMENTS_FOLD_AT = 5
/** The counter appears this close to the cap. */
const COUNTER_FROM = MAX_COMMENT_CHARS - 200

export interface CommentablePart {
  key: string
  label: string
  anchor: CommentAnchor
}

function snippet(text: string): string {
  const flat = text.replace(/[*_`#>[\]]/g, '').replace(/\s+/g, ' ').trim()
  return flat.length > 40 ? `${flat.slice(0, 40)}…` : flat
}

function tableLabel(doc: ReportDocument, tableId: string): string {
  return `Table · ${doc.tables[tableId]?.caption || tableId}`
}

/** The parts of a section a revision can edit, in order, with their labels. */
export function commentableParts(section: Section, doc: ReportDocument): CommentablePart[] {
  const parts: CommentablePart[] = []
  let paragraphs = 0
  let lists = 0
  section.blocks.forEach((b: Block, i) => {
    if (b.type === 'paragraph') {
      paragraphs += 1
      parts.push({ key: `b${i}`, label: `Paragraph ${paragraphs} · ${snippet(b.md)}`,
        anchor: { section_id: section.section_id, block_index: i } })
    } else if (b.type === 'bullets') {
      lists += 1
      parts.push({ key: `b${i}`, label: `Bullet list ${lists} · ${snippet(b.items[0] ?? '')}`,
        anchor: { section_id: section.section_id, block_index: i } })
    } else if (b.type === 'table_ref' && doc.tables[b.table_id]
      && !parts.some(p => p.anchor.table_id === b.table_id)) {
      parts.push({ key: `t${b.table_id}`, label: tableLabel(doc, b.table_id),
        anchor: { section_id: section.section_id, table_id: b.table_id } })
    }
  })
  return parts
}

/** A comment's anchor in words, read against the shown version. */
export function anchorWords(anchor: CommentAnchor, section: Section, doc: ReportDocument): string {
  if (anchor.table_id) return tableLabel(doc, anchor.table_id)
  if (anchor.block_index == null) return 'Whole section'
  const block = section.blocks[anchor.block_index]
  const nth = (type: Block['type']) =>
    section.blocks.slice(0, anchor.block_index! + 1).filter(b => b.type === type).length
  if (block?.type === 'paragraph') return `Paragraph ${nth('paragraph')}`
  if (block?.type === 'bullets') return `Bullet list ${nth('bullets')}`
  return `Part ${anchor.block_index + 1}`
}

const ORDER: Record<ReportComment['status'], number> = { open: 0, addressed: 1, withdrawn: 2 }

function StatusTag({ c }: { c: ReportComment }) {
  if (c.status === 'open') return <Tag tone="accent">Open</Tag>
  if (c.status === 'addressed') {
    return <Tag tone="ok">{c.revision ? `Addressed in v${c.revision.version}` : 'Addressed'}</Tag>
  }
  return <Tag tone="neutral">Withdrawn</Tag>
}

function CommentRow({
  c, section, doc, mine, project, reportId, onChanged,
}: {
  c: ReportComment
  section: Section
  doc: ReportDocument
  mine: boolean
  project: string
  reportId: string
  onChanged: (message: string) => void
}) {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)
  const closed = c.status !== 'open'

  async function withdraw() {
    setBusy(true)
    setRefusal(null)
    try {
      await withdrawReportComment(project, reportId, c.comment_id)
      setConfirming(false)
      onChanged('Comment withdrawn')
    } catch (err) {
      setRefusal(commentRefusalWords(isReportError(err) ? err.detail.error_kind : null,
        isReportError(err) ? err.detail.message : null))
    } finally {
      setBusy(false)
    }
  }

  return (
    <li
      className={`flex flex-col gap-0.5 py-1.5 ${closed ? 'text-muted' : 'text-text'}`}
      data-testid="report-comment"
      data-status={c.status}
    >
      <div className="flex items-center gap-2 text-[10.5px] text-muted">
        <span className="text-text font-medium">{anchorWords(c.anchor, section, doc)}</span>
        <StatusTag c={c} />
        <span>{[c.created_by, c.created_at.slice(0, 10)].filter(Boolean).join(' · ')}</span>
        {mine && c.status === 'open' && !confirming && (
          <button
            type="button"
            className="ml-auto underline text-muted hover:text-text"
            onClick={() => { setConfirming(true); setRefusal(null) }}
            data-testid="comment-withdraw"
          >
            Withdraw
          </button>
        )}
      </div>
      <p className={`text-[11.5px] whitespace-pre-wrap ${closed ? 'text-muted' : 'text-text'}`}>{c.text}</p>
      {c.status === 'open' && c.reason && (
        <p className="text-[10.5px] text-muted" data-testid="comment-reason">
          {DRAFT_REASON_WORDS[c.reason] ?? DRAFT_REASON_WORDS.draft_unavailable}
        </p>
      )}
      {confirming && (
        <div className="flex items-center gap-2 text-[11px]" data-testid="comment-withdraw-confirm">
          <span className="text-text">Withdraw this comment? It cannot be reopened.</span>
          <button type="button" className="underline text-text hover:text-accent" disabled={busy}
            onClick={() => { void withdraw() }} data-testid="comment-withdraw-yes">
            {busy ? 'Withdrawing…' : 'Withdraw'}
          </button>
          <span className="text-muted" aria-hidden="true">·</span>
          <button type="button" className="underline text-muted hover:text-text" disabled={busy}
            onClick={() => { setConfirming(false); setRefusal(null) }}>
            Keep
          </button>
        </div>
      )}
      {refusal && (
        <p role="status" className="text-[11px] text-warn" data-testid="comment-row-refusal">{refusal}</p>
      )}
    </li>
  )
}

/** What the viewer hands each section card for its comments. */
export interface CommentingProps {
  /** This section's comments (any status). */
  comments: ReportComment[]
  project: string
  reportId: string
  /** The digest the comments read returned; null while it is loading. */
  digest: string | null
  myEmail: string | null
  /** True when the latest version is on screen and its comments are read. */
  canComment: boolean
  /** A comment was posted or withdrawn: refetch, then toast `message`. */
  onChanged: (message: string) => void
  /** Reload the latest version and the comments (after a stale base). */
  onReload: () => void
}

export function SectionComments({
  section, doc, parts, formOpen, onCloseForm,
  comments, project, reportId, digest, myEmail, onChanged, onReload,
}: CommentingProps & {
  section: Section
  doc: ReportDocument
  parts: CommentablePart[]
  formOpen: boolean
  onCloseForm: () => void
}) {
  const textId = useId()
  const anchorId = useId()
  const [partKey, setPartKey] = useState<string>('')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState<{ kind: string | null; words: string } | null>(null)
  const [showAll, setShowAll] = useState(false)

  const chosen = parts.find(p => p.key === partKey) ?? parts[0]
  // A kept choice that is gone after a reload: keep the text, say so (UX 34, change 2).
  const anchorGone = partKey !== '' && !parts.some(p => p.key === partKey)
  const sorted = [...comments].sort((a, b) => ORDER[a.status] - ORDER[b.status])
  const shown = showAll ? sorted : sorted.slice(0, COMMENTS_FOLD_AT)
  const hidden = sorted.length - shown.length

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (busy || !chosen || !digest) return
    const body = text.trim()
    if (!body || body.length > MAX_COMMENT_CHARS) {
      setRefusal({ kind: 'invalid_comment', words: commentRefusalWords('invalid_comment', null) })
      return
    }
    setBusy(true)
    setRefusal(null)
    try {
      await addReportComment(project, reportId, body, chosen.anchor, digest)
      setText('')
      setPartKey('')
      onCloseForm()
      onChanged('Comment posted')
    } catch (err) {
      const kind = isReportError(err) ? err.detail.error_kind : null
      // Pin the part the user meant, so a reload cannot move it silently.
      if (kind === 'revision_stale_base') setPartKey(chosen.key)
      setRefusal({ kind, words: commentRefusalWords(kind, isReportError(err) ? err.detail.message : null) })
    } finally {
      setBusy(false)
    }
  }

  if (!formOpen && comments.length === 0) return null
  return (
    <div className="px-4 py-2 border-b border-border text-[11px]" data-testid="section-report-comments">
      {formOpen && chosen && (
        <form onSubmit={submit} className="flex flex-col gap-1.5 pb-2" data-testid="comment-form">
          <div className="flex items-center gap-2">
            <label htmlFor={anchorId} className="text-muted shrink-0">On</label>
            <select
              id={anchorId}
              className="min-w-0 max-w-full px-2 py-1 text-[11px] border border-border rounded bg-bg text-text"
              value={anchorGone ? '' : chosen.key}
              onChange={e => { setPartKey(e.target.value); setRefusal(null) }}
              disabled={busy}
              data-testid="comment-anchor"
            >
              {anchorGone && <option value="" disabled>Choose a part</option>}
              {parts.map(p => <option key={p.key} value={p.key}>{p.label}</option>)}
            </select>
          </div>
          <label htmlFor={textId} className="sr-only">Comment</label>
          <textarea
            id={textId}
            className="w-full min-h-[52px] px-2 py-1 text-[11.5px] border border-border rounded bg-bg text-text"
            value={text}
            onChange={e => { setText(e.target.value); if (refusal?.kind === 'invalid_comment') setRefusal(null) }}
            placeholder="What should change in this part?"
            disabled={busy}
            autoFocus
            data-testid="comment-text"
          />
          <div className="flex items-center gap-2">
            <button
              type="submit"
              disabled={busy || !digest || anchorGone || !text.trim()}
              className="inline-flex items-center gap-1 px-2 py-1 border border-border rounded text-[10.5px] text-text hover:border-accent hover:text-accent disabled:opacity-40"
              data-testid="comment-submit"
            >
              {busy ? 'Posting…' : 'Post comment'}
            </button>
            <button
              type="button"
              onClick={() => { onCloseForm(); setRefusal(null) }}
              disabled={busy}
              className="px-2 py-1 text-[10.5px] text-muted hover:text-text"
            >
              Cancel
            </button>
            {text.length >= COUNTER_FROM && (
              <span className={`ml-auto text-[10px] ${text.length > MAX_COMMENT_CHARS ? 'text-warn' : 'text-muted'}`}
                data-testid="comment-counter">
                {text.length.toLocaleString('en-US')} / {MAX_COMMENT_CHARS}
              </span>
            )}
          </div>
          {(refusal || anchorGone) && (
            <p role="status" className="flex items-center gap-2 text-[11px] text-warn" data-testid="comment-refusal">
              <span>{anchorGone && refusal?.kind !== 'revision_stale_base'
                ? commentRefusalWords('report_comment_anchor_invalid', null)
                : refusal?.words}</span>
              {(refusal?.kind === 'revision_stale_base' || refusal?.kind === 'report_comment_anchor_invalid') && (
                <button
                  type="button"
                  className="underline text-text hover:text-accent"
                  onClick={() => { setRefusal(null); onReload() }}
                  data-testid="comment-reload"
                >
                  Reload
                </button>
              )}
            </p>
          )}
        </form>
      )}
      {comments.length > 0 && (
        <>
          <ul className="flex flex-col divide-y divide-border" aria-label="Comments" data-testid="comment-list">
            {shown.map(c => (
              <CommentRow
                key={c.comment_id}
                c={c}
                section={section}
                doc={doc}
                mine={(c.created_by ?? null) === myEmail}
                project={project}
                reportId={reportId}
                onChanged={onChanged}
              />
            ))}
          </ul>
          {hidden > 0 && (
            <button type="button" className="mt-1 text-[10.5px] underline text-muted hover:text-text"
              onClick={() => setShowAll(true)} data-testid="comments-show-all">
              ({hidden} more) Show all
            </button>
          )}
        </>
      )}
    </div>
  )
}
