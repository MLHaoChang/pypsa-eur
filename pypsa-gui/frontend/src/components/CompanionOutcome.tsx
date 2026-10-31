import { useEffect, useState } from 'react'
import { useChatStore, type ChatMessage } from '../store/chatStore'
import { useUIStore } from '../store/uiStore'
import { getOwnedCheckedAnswer, type OwnedCheckedAnswer } from '../api/checkedAnswers'
import { CheckedEvidenceCard } from './CheckedEvidenceCard'
import { CheckedChart } from './CheckedChart'
import ChatMarkdown from './ChatMarkdown'

/** The one word for an answer the gate did not check; the chat panel's turn mark uses it too. */
export const ANSWER_NOT_CHECKED = 'Answer not checked'

const UPLOAD_BLOB = /^\/api\/projects\/[^/?#]+\/uploads\/[0-9a-f]{16}\/blob$/

export function downloadHref(result: unknown): { href: string; filename: string | null } | null {
  if (!result || typeof result !== 'object' || Array.isArray(result) || !('download_url' in result)
    || '_assurance' in result) return null
  const record = result as { download_url?: unknown; filename?: unknown }
  const href = record.download_url
  if (typeof href !== 'string' || href.length > 512 || !UPLOAD_BLOB.test(href)) return null
  return { href, filename: typeof record.filename === 'string' && record.filename.length <= 180 ? record.filename : null }
}

function afterLastUser(messages: readonly ChatMessage[]): readonly ChatMessage[] {
  let lastUser = -1
  for (let i = 0; i < messages.length; i += 1) if (messages[i].role === 'user') lastUser = i
  return messages.slice(lastUser + 1)
}

function checkedLabel(answer: OwnedCheckedAnswer): string {
  const receipt = answer.receipt
  if (receipt.result_slots?.some(s => s.disposition === 'needs_clarification')) return 'Clarification needed'
  if (receipt.request_complete === true) return 'Requested results checked'
  if (receipt.checked_block_ids.length === 0) return 'Requested results unavailable'
  return 'Checked evidence and limitations'
}

function OutcomeCard({ message, checked, failed, download, docked }: {
  message: ChatMessage | null; checked: boolean; failed: boolean;
  download: ReturnType<typeof downloadHref>; docked: boolean;
}) {
  // Docked (a full-screen tab, where the launcher sits over page content): the
  // answer starts as a one-line chip in the launcher's footprint. Opening it is
  // the user's act; it never closes on a timer.
  const [open, setOpen] = useState(!docked)
  const [verified, setVerified] = useState<{ message: ChatMessage; answer: OwnedCheckedAnswer } | null>(null)
  const [unavailable, setUnavailable] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    setVerified(null)
    setUnavailable(false)
    if (!checked || failed || !message?.answer_id || !message.receipt) return
    const controller = new AbortController()
    void getOwnedCheckedAnswer(message, controller.signal).then(answer => {
      if (!controller.signal.aborted) setVerified({ message, answer })
    }).catch(() => {
      if (!controller.signal.aborted) setUnavailable(true)
    })
    return () => controller.abort()
  }, [message, checked, failed, revision])
  useEffect(() => {
    if (!checked) return
    const refresh = () => { setVerified(null); setRevision(r => r + 1) }
    window.addEventListener('focus', refresh)
    return () => window.removeEventListener('focus', refresh)
  }, [checked])
  const answer = !failed && verified?.message === message ? verified.answer : null
  const missing = checked && (failed || unavailable || !message?.answer_id || !message.receipt)
  const ordinaryText = message?.content ?? ''
  const tooLarge = new TextEncoder().encode(ordinaryText).byteLength > 64 * 1024
  const body = checked ? answer?.text : tooLarge ? null : ordinaryText
  const label = checked ? answer ? checkedLabel(answer) : missing ? 'Checked answer unavailable' : 'Checking saved answer'
    : failed || message?.interrupted ? 'Response incomplete: not checked' : ANSWER_NOT_CHECKED
  const openTranscript = () => useUIStore.getState().setAssistantDockOpen(true)
  if (!open) {
    return <section data-testid="companion-outcome" data-docked="true" data-no-panel-close aria-label="Assistant answer"
      className="w-max max-w-[10rem] rounded-xl border border-border bg-bg-2 px-3 py-2 shadow-lg text-xs text-text">
      <p className="font-medium" role="status">{label}</p>
      <div className="mt-1 flex flex-wrap gap-x-3">
        <button type="button" className="underline" aria-expanded={false} data-testid="companion-outcome-show"
          onClick={() => setOpen(true)}>Show answer</button>
        {!checked && download && <a data-testid="companion-download" className="underline"
          href={download.href} download={download.filename ?? undefined}>Download</a>}
        {/* A checked chart's download is bound to its verified answer (S10). */}
        {answer?.receipt.chart && <CheckedChart chart={answer.receipt.chart} compact />}
      </div>
      {/* The same words as the transcript (LC-C7): read by screen readers, shown when opened. */}
      {body && <div className="sr-only" data-testid="companion-answer-text"><ChatMarkdown>{body}</ChatMarkdown></div>}
    </section>
  }
  // Opened on a full-screen tab it may sit over page content: capped, scrolls inside (UX, #446).
  return <section data-testid="companion-outcome" data-no-panel-close
    data-answer-id={answer?.answer_id} data-answer-sha256={answer?.receipt.content_sha256}
    aria-label="Assistant answer"
    className={`w-80 max-w-[calc(100vw-3rem)] rounded-xl border border-border bg-bg-2 p-3 shadow-lg text-xs text-text ${
      docked ? 'max-h-[60vh] overflow-y-auto' : ''}`}>
    <p className="font-medium" role="status">{label}</p>
    {answer ? <p className="mt-1 break-words text-[10px] text-muted">
      Project: {answer.project_id} · {answer.receipt.request_scope?.retain ? 'Retained evidence' : 'Recorded answer'}
    </p> : !checked && <p className="mt-1 break-words text-[10px] text-muted">
      {message?.origin_project !== undefined ? `Conversation: ${message.origin_project ?? 'Unsaved network'}` : 'Conversation source not recorded'}
    </p>}
    {body && <div className={`mt-2 overflow-auto break-words ${expanded ? 'max-h-[40vh]' : 'max-h-32'}`}
      tabIndex={0} aria-label="Canonical answer text" data-testid="companion-answer-text">
      <ChatMarkdown>{body}</ChatMarkdown>
    </div>}
    {tooLarge && !checked && <p className="mt-2">The full answer is available in the transcript.</p>}
    {checked && !answer && <p className="mt-2 text-muted">
      {missing ? 'No checked result is shown. Open the transcript or refresh evidence.' : 'Revalidating access, source and exact answer text.'}
    </p>}
    {answer && <div className="max-h-[25vh] overflow-auto break-words">
      <CheckedEvidenceCard answerId={answer.answer_id} receipt={answer.receipt}
        sourceLabel={answer.receipt.request_scope?.retain ? 'Retained evidence' : 'Recorded evidence'} />
      {answer.receipt.chart && <CheckedChart chart={answer.receipt.chart} profile={answer.receipt.request_scope?.profile} entity={answer.receipt.request_scope?.entity_id} />}
    </div>}
    <div className="mt-2 flex flex-wrap gap-3">
      {body && <button type="button" className="underline" aria-expanded={expanded}
        onClick={() => setExpanded(v => !v)}>{expanded ? 'Collapse answer' : 'Expand answer'}</button>}
      <button type="button" className="underline" onClick={openTranscript}>Open transcript</button>
      {docked && <button type="button" className="underline" aria-expanded={true} data-testid="companion-outcome-hide"
        onClick={() => setOpen(false)}>Hide answer</button>}
      {checked && <button type="button" className="underline" onClick={() => {
        setVerified(null); setRevision(r => r + 1)
      }}>Refresh evidence</button>}
      {answer && <a className="underline" href={`/api/chat/answers/${encodeURIComponent(answer.answer_id)}`}
        target="_blank" rel="noopener noreferrer">View answer record</a>}
      {!checked && download && <a data-testid="companion-download" className="underline"
        href={download.href} download={download.filename ?? undefined}>Download</a>}
    </div>
  </section>
}

/** Latest completed turn only. No model paraphrase, timed dismissal or raw checked artifact admission.
 * `docked`: starts as a one-line chip (full-screen tabs, where it would cover page content). */
export default function CompanionOutcome({ hidden = false, docked = false }: { hidden?: boolean; docked?: boolean }) {
  const messages = useChatStore(s => s.messages)
  const streaming = useChatStore(s => s.streaming)
  const error = useChatStore(s => s.error)
  const session = useChatStore(s => s.sessionId)
  const sequence = useChatStore(s => s.newChatSeq)
  const generation = useChatStore(s => s.utteranceGeneration)
  const profile = useChatStore(s => s.profileId ?? s.boundProfileId)
  const project = useUIStore(s => s.currentProject)
  const mismatch = useUIStore(s => s.projectMismatch)
  const turn = afterLastUser(messages)
  const message = [...turn].reverse().find(m => m.role === 'assistant' && m.content.trim()) ?? null
  const user = [...messages].reverse().find(m => m.role === 'user')
  const wrongProject = [message?.origin_project, user?.origin_project]
    .some(origin => origin !== undefined && origin !== project)
  const checked = error?.error_kind.startsWith('assurance_') === true
    || user?.requiresChecked === true || turn.some(m => m.requiresChecked || m.receipt || m.answer_id
    || (m.tool_result && typeof m.tool_result === 'object' && '_assurance' in m.tool_result))
  const download = checked ? null : [...turn].reverse().map(m => downloadHref(m.tool_result)).find(Boolean) ?? null
  if (hidden || streaming || mismatch || wrongProject
    || (!message && !download && !error)) return null
  const key = JSON.stringify([project, session, sequence, generation, profile, user?.id])
  return <OutcomeCard key={key} message={message} checked={checked} failed={!!error} download={download} docked={docked} />
}
