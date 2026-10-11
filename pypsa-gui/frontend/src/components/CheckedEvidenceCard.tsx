import type { AnswerReceipt } from '../utils/answerAssurance'

export function CheckedEvidenceCard({ answerId, receipt }: { answerId: string; receipt: AnswerReceipt }) {
  const scope = receipt.request_scope
  return <details className="mt-2 text-[11px] border border-border rounded p-2" data-testid="checked-evidence-card">
    <summary className="cursor-pointer">{receipt.request_complete === true ? 'Requested results checked' : 'Checked evidence and limitations'}</summary>
    <div className="mt-2 space-y-1">
      {scope && <p>{scope.retain ? 'Retained run' : 'Current run'} · {scope.entity_id} · {scope.profile}</p>}
      {scope?.start && <p>{scope.start} to {scope.end}</p>}
      {receipt.result_slots && <ul>{receipt.result_slots.map(s => <li key={s.metric}>
        {s.metric.replace(/_/g, ' ')}: {s.disposition}
        {s.valid_snapshots !== undefined && ` (${s.valid_snapshots}/${s.requested_snapshots} snapshots)`}
        {s.reason && ` — ${s.reason.replace(/_/g, ' ')}`}
      </li>)}</ul>}
      {receipt.limitations.length > 0 && <ul aria-label="Answer limitations">{receipt.limitations.map((text, i) => <li key={i}>{`Limitation: ${text}`}</li>)}</ul>}
      <p>{receipt.checked_block_ids.length} facts checked against evidence. This does not certify the engineering model.</p>
      {receipt.reviewer_status !== 'not_required' && <p>Interpretation review: {receipt.reviewer_status.replace(/_/g, ' ')}</p>}
      <div className="flex flex-wrap gap-2">
        <a className="underline" href={`/api/chat/answers/${encodeURIComponent(answerId)}/export?format=zip`} download>Download checked values and evidence</a>
        {receipt.evidence_ids.map((id, i) => <a key={id} className="underline" href={`/api/chat/evidence/${encodeURIComponent(id)}`} target="_blank" rel="noopener noreferrer">Evidence {i + 1}</a>)}
      </div>
    </div>
  </details>
}
