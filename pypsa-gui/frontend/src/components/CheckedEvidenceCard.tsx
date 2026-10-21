import type { AnswerReceipt } from '../utils/answerAssurance'
import { readableProfile, readableWindow } from '../utils/readableTime'
import type { NavigationEntry } from '../utils/answerAssurance'

// What the Load flow tab shows instead of the checked solve and window (S3b).
const VIEW_REASON: Record<NonNullable<NavigationEntry['reason']>, string> = {
  window_differs: 'a different time window',
  source_not_lopf: "results other than the optimisation's",
  inputs_changed: 'inputs changed since the checked run',
  fingerprint_basis_unknown: "a solve that can't be matched to the checked one",
  project_differs: 'another project',
}

/** The Load flow line: "showing" only with a confirmed, matching display. */
export function loadflowLine(entry: NavigationEntry): string | null {
  if (entry.shows === 'checked_evidence' && entry.window) {
    return `Load flow is showing the solve and window that were checked (${readableWindow(entry.window.start, entry.window.end)}).`
  }
  if (entry.shows === 'different_view' && entry.reason) {
    // Unknown basis: the mismatch itself is not known, so "may not be".
    const verb = entry.reason === 'fingerprint_basis_unknown' ? 'may not be' : 'are not'
    return `Load flow was opened on a different window or solve than the one checked. It shows ${VIEW_REASON[entry.reason]}, so the numbers there ${verb} the checked ones.`
  }
  return entry.shows === 'not_confirmed' ? 'Load flow opened.' : null
}

export function CheckedEvidenceCard({ answerId, receipt, sourceLabel }: { answerId: string; receipt: AnswerReceipt; sourceLabel?: string }) {
  const scope = receipt.request_scope
  const clarifying = receipt.result_slots?.some(s => s.disposition === 'needs_clarification') === true
  return <details className="mt-2 text-[11px] border border-border rounded p-2" data-testid="checked-evidence-card">
    <summary className="cursor-pointer">{clarifying ? 'Clarification needed' : receipt.request_complete === true ? 'Requested results checked' : 'Checked evidence and limitations'}</summary>
    <div className="mt-2 space-y-1">
      {scope && <p>{sourceLabel ?? (scope.retain ? 'Retained run' : 'Current run')} · {scope.entity_id} · {readableProfile(scope.profile)}</p>}
      {scope?.start && <p>{readableWindow(scope.start, scope.end)}</p>}
      {receipt.navigation?.map(n => loadflowLine(n)).filter(Boolean).map((line, i) =>
        <p key={i} data-testid="checked-loadflow-view">{line}</p>)}
      {scope?.cost_basis && <p>Cost basis: {scope.cost_basis.currency}, price year {scope.cost_basis.price_year};
        {' '}annual fixed cost fraction {scope.cost_basis.annual_fixed_cost_fraction}</p>}
      {receipt.result_slots && <ul>{receipt.result_slots.map(s => <li key={s.metric}>
        {s.metric.replace(/_/g, ' ')}: {s.disposition.replace(/_/g, ' ')}
        {s.valid_snapshots !== undefined && ` (${s.valid_snapshots}/${s.requested_snapshots} snapshots)`}
        {s.reason && ` — ${s.reason.replace(/_/g, ' ')}`}
      </li>)}</ul>}
      {receipt.limitations.length > 0 && <ul aria-label="Answer limitations">{receipt.limitations.map((text, i) => <li key={i}>{`Limitation: ${text}`}</li>)}</ul>}
      {clarifying ? <p>No result has been checked. Answer the clarification to continue.</p>
        : <p>{receipt.checked_block_ids.length} facts checked against evidence. This does not certify the engineering model.</p>}
      {receipt.reviewer_status !== 'not_required' && <p>Interpretation review: {receipt.reviewer_status.replace(/_/g, ' ')}</p>}
      <div className="flex flex-wrap gap-2">
        {!clarifying && <a className="underline" href={`/api/chat/answers/${encodeURIComponent(answerId)}/export?format=zip`} download>Download checked values and evidence</a>}
        {receipt.evidence_ids.map((id, i) => <a key={id} className="underline" href={`/api/chat/evidence/${encodeURIComponent(id)}`} target="_blank" rel="noopener noreferrer">Evidence {i + 1}</a>)}
      </div>
    </div>
  </details>
}
