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
