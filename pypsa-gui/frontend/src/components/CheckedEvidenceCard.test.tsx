import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { CheckedEvidenceCard } from './CheckedEvidenceCard'
import { parseApproval, type AnswerReceipt } from '../utils/answerAssurance'

afterEach(cleanup)
const receipt: AnswerReceipt = {
  policy_version: '1', content_sha256: 'a'.repeat(64), evidence_ids: ['evidence-1'],
  checked_block_ids: ['energy'], limitations: ['One requested result is unavailable.'],
  reviewer_status: 'withheld_timeout', request_complete: false,
  request_scope: { profile: 'generator-cost/1', entity_id: 'G1', retain: true },
  result_slots: [{ metric: 'total_cost', disposition: 'unavailable', reason: 'invalid_or_missing_raw_data' }],
}
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
