import type { SourceGapDraft } from '../store/chatStore'

/** Prepared display data; only the explicit Send button requests a turn. */
export function SourceGapFollowupCard({ draft, disabled, unavailable, send, dismiss }: {
  draft: SourceGapDraft; disabled: boolean; unavailable: boolean; send: () => void; dismiss: () => void
}) {
  return <section aria-label="Prepared checked follow-up" className="border-t border-border px-3 py-2 text-xs space-y-2 max-h-64 overflow-auto">
    <p className="font-medium">Review checked follow-up</p>
    <p>Your existing message and attachments stay in the composer. This separate request checks the scope below using the normal chat gates.</p>
    <dl>{Object.entries(draft.checked_scope).map(([name, value]) => <div key={name} className="break-all">
      <dt className="inline font-medium">{name.replace(/_/g, ' ')}: </dt>
      <dd className="inline whitespace-pre-wrap">{typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd>
    </div>)}</dl>
    <p className="break-all">Source gap: {draft.source_gap_ref.source_id} · Revision: {draft.source_gap_ref.revision}</p>
    <p>Sending explicitly requests a checked answer for this turn. It does not resolve the historical gap.</p>
    {unavailable && <p>Answer checks are unavailable. Retry availability before sending this request.</p>}
    <div className="flex flex-wrap gap-3">
      <button type="button" className="text-accent min-h-11 px-2" disabled={disabled} onClick={send}>Send checked follow-up</button>
      <button type="button" className="text-accent min-h-11 px-2" onClick={dismiss}>Dismiss follow-up</button>
    </div>
  </section>
}
