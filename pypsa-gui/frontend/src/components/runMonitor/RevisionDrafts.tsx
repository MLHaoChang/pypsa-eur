// SH-07d PR B: a finished run's report revision drafts, on its run card. The
// open comments on the run's own report, each with "Draft a fix"; the run's
// drafts, each with Confirm and Discard once it is drafted. Nothing changes
// the report until Confirm. Every model or user text (comment, edit, heading)
// renders as plain text: no markdown, no HTML, no link (GK B (68) R3).
import { useCallback, useEffect, useState } from 'react'
import {
  confirmRevisionDraft, discardRevisionDraft, getReport, isReportError, listReportComments,
  requestRevisionDraft,
  type CommentAnchor, type ReportComment, type ReportDocument, type RevisionDraft, type RevisionEdit,
} from '../../api/reports'
import type { RunMonitorView } from '../../api/runMonitor'
import { useUIStore } from '../../store/uiStore'
import {
  DRAFT_NOT_APPLIED, DRAFT_REASON_WORDS, DRAFT_STATUS_WORDS, draftErrorWords, draftRefusalWords,
} from './runWords'

export const DRAFT_POLL_MS = 4000
/** 07c's confirm refusals: the row was drafted, then not applied. */
const NOT_APPLIED = new Set(['revision_stale_base', 'report_revision_unsupported'])

/** Where a comment sits, in words: the section heading and the block. */
export function anchorWords(anchor: CommentAnchor, doc: ReportDocument | null): string {
  const section = doc?.sections.find(s => s.section_id === anchor.section_id)
  if (!section) return 'A part of the report'
  if (anchor.table_id) {
    const table = section.blocks.find(b => b.type === 'table_ref' && b.table_id === anchor.table_id)
    const caption = table?.type === 'table_ref' ? table.caption : null
    return `${section.heading}, table${caption ? ` "${caption}"` : ''}`
  }
  const i = anchor.block_index
  const block = i == null ? undefined : section.blocks[i]
  if (block?.type === 'paragraph') {
    const n = section.blocks.slice(0, i! + 1).filter(b => b.type === 'paragraph').length
    return `${section.heading}, paragraph ${n}`
  }
  if (block?.type === 'bullets') return `${section.heading}, bullet list`
  return section.heading
}

function EditText({ edit }: { edit: RevisionEdit }) {
  if (edit.kind === 'paragraph_text') {
    return <p className="whitespace-pre-wrap" data-testid="draft-edit">{edit.md}</p>
  }
  if (edit.kind === 'bullet_text') {
    return <ul className="list-disc pl-5" data-testid="draft-edit">{edit.items.map((t, i) => <li key={i}>{t}</li>)}</ul>
  }
  if (edit.kind === 'table_text') {
    return <div data-testid="draft-edit">
      {edit.caption && <p>Caption: {edit.caption}</p>}
      <p>Columns: {edit.columns.join(', ')}</p>
    </div>
  }
  return <table className="text-left" data-testid="draft-edit"><tbody>
    {edit.rows.map((row, r) => <tr key={r}>{row.map((cell, c) => <td key={c} className="pr-2">{cell}</td>)}</tr>)}
  </tbody></table>
}

export default function RevisionDrafts({ taskId, view, onRefresh }: {
  taskId: string; view: RunMonitorView; onRefresh: () => Promise<void>
}) {
  const current = useUIStore(s => s.currentProject)
  // The run belongs to the project the card opened in: under another project
  // the block reads, polls and sends nothing (R5).
  const [home] = useState(current)
  const project = current !== null && current === home ? current : null
  const offer = view.monitor.revision_report ?? null
  const rows = view.monitor.revision_drafts ?? []
  const reportId = offer?.report_id ?? rows[0]?.report_id ?? null
  const [comments, setComments] = useState<ReportComment[] | null>(null)
  const [doc, setDoc] = useState<ReportDocument | null>(null)
  const [readFailed, setReadFailed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)

  const inFlightStates = offer?.in_flight ?? ['requested', 'drafting']
  const inFlight = rows.some(r => inFlightStates.includes(r.status))
  const settledKey = rows.map(r => `${r.id}:${r.status}`).join(',')

  const load = useCallback(async () => {
    if (!project || !reportId) return
    try {
      setComments((await listReportComments(project, reportId)).comments)
      setReadFailed(false)
    } catch {
      setReadFailed(true)
    }
  }, [project, reportId])

  // The comments again whenever a row changes: a refused draft notes its comment.
  useEffect(() => { void load() }, [load, settledKey])
  useEffect(() => {
    if (!project || !reportId) return
    let live = true
    getReport(project, reportId).then(d => { if (live) setDoc(d) }).catch(() => undefined)
    return () => { live = false }
  }, [project, reportId])

  // The card stops polling once the run is over; a draft in flight polls here,
  // one interval at a time, ended on unmount or a project switch (R5).
  useEffect(() => {
    if (!inFlight || !project) return
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void onRefresh()
    }, DRAFT_POLL_MS)
    return () => window.clearInterval(timer)
  }, [inFlight, project, onRefresh])

  if (!offer && rows.length === 0) return null
  if (!project || !reportId) return null
  const mayAct = offer?.may_act === true
  const atLimit = offer ? rows.length >= offer.max_requests : true
  const open = (comments ?? []).filter(c => c.status === 'open')
  const byId = new Map((comments ?? []).map(c => [c.comment_id, c]))
  const blocked = inFlight ? 'A draft is being made: wait for it before asking for another.'
    : atLimit && offer ? `This run has made its ${offer.max_requests} drafts.` : null

  const act = async (call: () => Promise<unknown>) => {
    setBusy(true)
    setRefusal(null)
    try {
      await call()
    } catch (err) {
      setRefusal(draftRefusalWords(isReportError(err) ? err.detail.error_kind : null,
        isReportError(err) ? err.detail.message : null))
    } finally {
      await onRefresh()
      await load()
      setBusy(false)
    }
  }

  return <div className="mt-1" data-testid="revision-drafts">
    {offer && <section aria-label="Report comments" data-testid="revision-comments">
      <div className="font-medium">Report comments</div>
      {readFailed && <p className="text-muted" role="status">The report's comments could not be read just now.</p>}
      {comments && open.length === 0 && <p className="text-muted" data-testid="revision-no-comments">
        No open comments on this run's report.</p>}
      <ul className="space-y-1">
        {open.map(c => <li key={c.comment_id} data-testid="revision-comment" data-comment={c.comment_id}>
          <div className="text-muted">{anchorWords(c.anchor, doc)}</div>
          <div className="whitespace-pre-wrap">{c.text}</div>
          {c.reason && <div className="text-muted" data-testid="revision-comment-reason">
            {DRAFT_REASON_WORDS[c.reason] ?? DRAFT_REASON_WORDS.draft_unavailable}</div>}
          {mayAct && <button type="button" disabled={busy || blocked !== null}
            className="text-accent hover:underline disabled:opacity-40" data-testid="revision-draft-request"
            aria-describedby={blocked ? `revision-blocked-${taskId}` : undefined}
            onClick={() => void act(() => requestRevisionDraft(project, reportId, c.comment_id, taskId))}>
            Draft a fix</button>}
        </li>)}
      </ul>
      {mayAct && blocked && open.length > 0 && <p id={`revision-blocked-${taskId}`} className="text-muted" role="status"
        data-testid="revision-blocked">{blocked}</p>}
    </section>}

    {rows.length > 0 && <section aria-label="Drafts" data-testid="revision-draft-list">
      <div className="font-medium">Drafts</div>
      <ul className="space-y-1">
        {rows.map(r => {
          const comment = byId.get(r.comment_id)
          const status = r.status === 'refused' && r.error && NOT_APPLIED.has(r.error)
            ? DRAFT_NOT_APPLIED : DRAFT_STATUS_WORDS[r.status] ?? 'Not made'
          const reasons = (r.reasons ?? []).map(k => DRAFT_REASON_WORDS[k]).filter(Boolean)
          return <li key={r.id} data-testid="revision-draft" data-status={r.status}>
            <div role="status" data-testid="revision-draft-status">{status}</div>
            {comment && <div className="text-muted">For: {comment.text}</div>}
            {r.edit && ['drafted', 'applying', 'applied'].includes(r.status) && <>
              <div className="text-muted">{r.status === 'applied' ? 'Applied edit:' : 'Proposed edit:'}</div>
              <EditText edit={r.edit} />
            </>}
            {r.status === 'refused' && <div className="text-muted" data-testid="revision-draft-why">
              {draftErrorWords(r.error)}</div>}
            {reasons.length > 0 && <ul className="list-disc pl-5 text-muted" data-testid="revision-draft-reasons">
              {reasons.map((t, i) => <li key={i}>{t}</li>)}</ul>}
            {r.status === 'drafted' && mayAct && <div className="flex flex-wrap items-baseline gap-2">
              <button type="button" disabled={busy} className="text-accent hover:underline disabled:opacity-40"
                data-testid="revision-draft-confirm"
                onClick={() => void act(() => confirmRevisionDraft(project, reportId, r.id, taskId))}>Confirm</button>
              <button type="button" disabled={busy} className="text-muted hover:text-text disabled:opacity-40"
                data-testid="revision-draft-discard"
                onClick={() => void act(() => discardRevisionDraft(project, reportId, r.id, taskId))}>Discard</button>
            </div>}
          </li>
        })}
      </ul>
    </section>}
    {refusal && <p className="text-amber-400" role="status" data-testid="revision-refusal">{refusal}</p>}
  </div>
}
