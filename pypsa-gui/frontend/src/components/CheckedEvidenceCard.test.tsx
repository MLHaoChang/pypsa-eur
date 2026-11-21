import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { CheckedEvidenceCard, loadflowLine } from './CheckedEvidenceCard'
import { parseApproval, WARNED_LABEL, warningText, WARNING_REASON_CODES, type AnswerReceipt } from '../utils/answerAssurance'

afterEach(cleanup)
const receipt: AnswerReceipt = {
  policy_version: '1', content_sha256: 'a'.repeat(64), evidence_ids: ['evidence-1'],
  checked_block_ids: ['energy'], limitations: ['One requested result is unavailable.'],
  reviewer_status: 'withheld_timeout', request_complete: false,
  request_scope: { profile: 'generator-cost/1', entity_id: 'G1', retain: true },
  result_slots: [{ metric: 'total_cost', disposition: 'unavailable', reason: 'invalid_or_missing_raw_data' }],
}
it('displays the declared cost basis and rejects malformed basis metadata', () => {
  const scoped = { ...receipt, request_scope: { ...receipt.request_scope!, cost_basis: {
    currency: 'EUR' as const, price_year: 2026, annual_fixed_cost_fraction: 0.5,
  } } }
  render(<CheckedEvidenceCard answerId="answer-1" receipt={scoped} sourceLabel="Recorded evidence" />)
  expect(screen.getByText(/Cost basis: EUR, price year 2026; annual fixed cost fraction 0.5/)).toBeTruthy()
  expect(screen.getByText(/Recorded evidence/)).toBeTruthy()
  const frame = { version: 1, answer_id: 'a', turn_id: 't', verdict: 'limited', receipt: scoped }
  expect(parseApproval(frame)).not.toBeNull()
  for (const cost_basis of [
    { ...scoped.request_scope.cost_basis, currency: 'USD' },
    { ...scoped.request_scope.cost_basis, price_year: '2026' },
    { ...scoped.request_scope.cost_basis, annual_fixed_cost_fraction: NaN },
    { ...scoped.request_scope.cost_basis, annual_fixed_cost_fraction: -1 },
  ]) expect(parseApproval({ ...frame, receipt: { ...scoped, request_scope: { ...scoped.request_scope, cost_basis } } })).toBeNull()
})
it('shows unavailable slots, retained scope and interpretation limits without claiming completeness', () => {
  render(<CheckedEvidenceCard answerId="answer-1" receipt={receipt} />)
  expect(screen.getByText('Checked evidence and limitations')).toBeTruthy()
  expect(screen.queryByText('Requested results checked')).toBeNull()
  expect(screen.getByText(/total cost: unavailable/)).toBeTruthy()
  expect(screen.getByText(/Retained run/)).toBeTruthy()
  expect(screen.getByText(/Interpretation review: withheld timeout/)).toBeTruthy()
  expect(screen.getByRole('link', { name: 'Download checked values and evidence', hidden: true }).getAttribute('href'))
    .toBe('/api/chat/answers/answer-1/export?format=zip')
})
it('accepts bounded withheld review receipts but rejects malformed scope/coverage', () => {
  const frame = { version: 1, answer_id: 'a', turn_id: 't', verdict: 'limited', receipt }
  expect(parseApproval(frame)).not.toBeNull()
  expect(parseApproval({ ...frame, receipt: { ...receipt, request_scope: { profile: 'p', entity_id: { injected: true } } } })).toBeNull()
  expect(parseApproval({ ...frame, receipt: { ...receipt, request_complete: 'yes' } })).toBeNull()
  expect(parseApproval({ ...frame, receipt: { ...receipt, result_slots: [{ metric: 'cost', disposition: 'answered', requested_snapshots: -1 }] } })).toBeNull()
})
it('refuses array-shaped verdict and review status even with an otherwise valid receipt', () => {
  const frame = { version: 1, answer_id: 'a', turn_id: 't', verdict: 'limited', receipt }
  expect(parseApproval({ ...frame, verdict: ['limited'] })).toBeNull()
  expect(parseApproval({ ...frame, receipt: { ...receipt, reviewer_status: ['passed'] } })).toBeNull()
})

it('renders clarification as a question without a checked-results badge or value download', () => {
  const questionReceipt: AnswerReceipt = { ...receipt, checked_block_ids: [], evidence_ids: [], reviewer_status: 'not_required',
    result_slots: [{ metric: 'total_cost', disposition: 'needs_clarification', reason: 'cost_quantity_required' }] }
  render(<CheckedEvidenceCard answerId="question" receipt={questionReceipt} />)
  expect(screen.getByText('Clarification needed')).toBeTruthy()
  expect(screen.getByText('No result has been checked. Answer the clarification to continue.')).toBeTruthy()
  expect(screen.queryByText('Requested results checked')).toBeNull()
  expect(screen.queryByRole('link', { name: 'Download checked values and evidence', hidden: true })).toBeNull()
  expect(parseApproval({ version: 1, answer_id: 'a', turn_id: 't', verdict: 'limited', receipt: questionReceipt })).not.toBeNull()
  expect(parseApproval({ version: 1, answer_id: 'a', turn_id: 't', verdict: 'limited', receipt: { ...questionReceipt,
    result_slots: [{ metric: 'cost', disposition: ['needs_clarification'] }] } })).toBeNull()
})

it('rejects a clarification receipt that claims checked facts or completion', () => {
  const question = { ...receipt, checked_block_ids: [], evidence_ids: [], reviewer_status: 'not_required',
    result_slots: [{ metric: 'total_cost', disposition: 'needs_clarification' }] }
  const frame = { version: 1, answer_id: 'a', turn_id: 't', verdict: 'limited', receipt: question }
  expect(parseApproval(frame)).not.toBeNull()
  expect(parseApproval({ ...frame, verdict: 'passed' })).toBeNull()
  expect(parseApproval({ ...frame, receipt: { ...question, request_complete: true } })).toBeNull()
  expect(parseApproval({ ...frame, receipt: { ...question, checked_block_ids: ['invented'] } })).toBeNull()
  expect(parseApproval({ ...frame, receipt: { ...question, evidence_ids: ['unrelated'] } })).toBeNull()
})

describe('the Load flow line (S3b)', () => {
  const base = { action_id: 'x', outcome: 'completed' }
  it('says showing only for a confirmed, matching display', () => {
    expect(loadflowLine({ ...base, shows: 'checked_evidence',
      window: { start: '2026-01-01T00:00:00', end: '2026-01-01T23:00:00' } }))
      .toBe('Load flow is showing the solve and window that were checked (1 Jan 2026, 00:00–23:00).')
    expect(loadflowLine({ ...base, shows: 'different_view', reason: 'window_differs' }))
      .toBe('Load flow was opened on a different window or solve than the one checked. It shows a different time '
        + 'window, so the numbers there are not the checked ones.')
    expect(loadflowLine({ ...base, shows: 'different_view', reason: 'fingerprint_basis_unknown' }))
      .toBe("Load flow opened. It shows a solve that can't be matched to the checked one, so the numbers there may not be the checked ones.")
    expect(loadflowLine({ ...base, outcome: 'submitted', shows: 'not_confirmed' })).toBe('Load flow opened.')
    expect(loadflowLine(base)).toBeNull()
  })
  it('refuses a forged navigation join', () => {
    const frame = (navigation: unknown) => ({ version: 1, answer_id: 'a', turn_id: 't', verdict: 'limited',
      receipt: { policy_version: '1', content_sha256: 'c'.repeat(64), evidence_ids: [], checked_block_ids: [],
        limitations: [], reviewer_status: 'not_required', navigation } })
    expect(parseApproval(frame([{ ...base, shows: 'checked_evidence', window: { start: 'a', end: 'b' } }]))).not.toBeNull()
    for (const forged of [[{ ...base, shows: 'checked_evidence' }], [{ ...base, shows: 'different_view', reason: 'made_up' }],
      [{ ...base, shows: 'shown' }]]) expect(parseApproval(frame(forged))).toBeNull()
  })
})

it('shows a required warning slot and reads the request incomplete (AA-13)', () => {
  const warned: AnswerReceipt = {
    ...receipt, policy_version: '5', reviewer_status: 'not_required', limitations: [],
    request_scope: { profile: 'ac-branch-loading/1', entity_id: 'L12' },
    result_slots: [
      { metric: 'ac_pf_convergence', disposition: 'warning', reason: 'ac_pf_not_converged (1 of 2 snapshots in the window converged)',
        valid_snapshots: 1, requested_snapshots: 2, excluded_indices: ['2026-01-01T01:00:00'] },
      { metric: 'branch_rating', disposition: 'answered', valid_snapshots: 1, requested_snapshots: 2 },
    ],
  }
  render(<CheckedEvidenceCard answerId="answer-1" receipt={warned} />)
  expect(screen.getByText(WARNED_LABEL)).toBeTruthy()
  const row = screen.getByTestId('checked-warning-slot')
  expect(row.textContent).toBe(
    'Warning: AC power flow did not converge for 1 of 2 snapshots in the window. The values cover 1 of 2 snapshots.')
  expect(row.className).toContain('text-warn')
  // Listed first, above the metric slots.
  expect(row.parentElement!.firstElementChild).toBe(row)
  expect(screen.queryByText('Requested results checked')).toBeNull()
})

it('shows a warning once, as its worded row, with no raw slot key or reason code (AA-13)', () => {
  const warned: AnswerReceipt = {
    ...receipt, policy_version: '5', reviewer_status: 'not_required',
    // The backend's receipt lists the warning slot again as a limitation.
    limitations: [
      'ac_pf_convergence: ac_pf_not_converged (2 of 3 snapshots in the window converged)',
      'Interpretation withheld: unavailable',
    ],
    request_scope: { profile: 'ac-branch-loading/1', entity_id: 'Line_BC' },
    result_slots: [
      { metric: 'ac_pf_convergence', disposition: 'warning', reason: 'ac_pf_not_converged (2 of 3 snapshots in the window converged)',
        valid_snapshots: 2, requested_snapshots: 3, excluded_indices: ['2030-01-01T02:00:00'] },
      { metric: 'ac_branch_rating', disposition: 'answered', valid_snapshots: 2, requested_snapshots: 3 },
    ],
  }
  render(<CheckedEvidenceCard answerId="answer-1" receipt={warned} />)
  const card = screen.getByTestId('checked-evidence-card')
  expect(card.textContent).not.toContain('ac_pf_')
  expect(screen.getAllByTestId('checked-warning-slot')).toHaveLength(1)
  // Any other limitation is still shown.
  expect(screen.getByText('Limitation: Interpretation withheld: unavailable')).toBeTruthy()
})

it('words each warning reason in plain text, with no slot key or dash (AA-13)', () => {
  // Parity with the backend's warning states (tests/test_ac_branch_loading_assurance.py reads this list).
  expect(WARNING_REASON_CODES).toEqual(['ac_pf_not_converged', 'ac_pf_convergence_unknown'])
  const slot = { metric: 'ac_pf_convergence', disposition: 'warning' as const, valid_snapshots: 2, requested_snapshots: 3 }
  expect(warningText({ ...slot, reason: 'ac_pf_convergence_unknown (2 of 3 snapshots in the window converged)' })).toBe(
    'Warning: AC power flow convergence is unknown for 1 of 3 snapshots in the window. The values cover 2 of 3 snapshots.')
  expect(warningText({ ...slot, reason: 'other_reason' })).toBe(
    'Warning: 1 of 3 snapshots in the window are excluded. The values cover 2 of 3 snapshots.')
  for (const code of WARNING_REASON_CODES) {
    const text = warningText({ ...slot, reason: `${code} (2 of 3)` })
    expect(text).not.toMatch(/[—_]|ac pf/)
  }
})
