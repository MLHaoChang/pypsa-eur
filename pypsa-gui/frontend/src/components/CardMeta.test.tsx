import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { metaLine } from './CardMeta'
import { CheckedEvidenceCard } from './CheckedEvidenceCard'
import type { AnswerReceipt } from '../utils/answerAssurance'

// Assistant-assurance 06 (A5.3, GK G4, UX 31): an unstated meta value is named
// under "Not stated", never shown as 0, "—" or blank.
afterEach(cleanup)

describe('metaLine', () => {
  it('joins stated values with "·" and ends with the unstated names', () => {
    expect(metaLine([['assurance', 'Checked'], ['source', null], ['time', '1 Jan 2026'], ['units', '']]))
      .toBe('Checked · 1 Jan 2026 · Not stated: source, units')
  })

  it.each([undefined, null, '', '  ', Number.NaN])('an unstated %s is never 0, "—" or blank', (value) => {
    const line = metaLine([['assurance', 'Checked'], ['units', value]])
    expect(line).toBe('Checked · Not stated: units')
    expect(line).not.toMatch(/(^|· )(0|—|-)( ·|$)|· ·/)
  })

  it('a stated zero is a value', () => {
    expect(metaLine([['count', 0]])).toBe('0')
  })
})

describe('checked evidence card meta line', () => {
  const base: AnswerReceipt = {
    policy_version: '1', content_sha256: 'a'.repeat(64), evidence_ids: ['e-1'], checked_block_ids: ['energy'],
    limitations: ['One requested result is unavailable.'], reviewer_status: 'not_required', request_complete: true,
    result_slots: [{ metric: 'energy', disposition: 'answered' }],
  }

  it('with units and source absent, they are named under "Not stated", and limitations keep their own line', () => {
    render(<CheckedEvidenceCard answerId="a-1" receipt={base} />)
    expect(screen.getByTestId('card-meta').textContent).toBe('Checked against evidence · Not stated: source, time, units')
    expect(screen.getByText('Limitation: One requested result is unavailable.').closest('[data-testid="card-meta"]')).toBeNull()
  })

  it('a full scope states every field in one line', () => {
    render(<CheckedEvidenceCard answerId="a-1" receipt={{ ...base, request_scope: {
      profile: 'generator-cost/1', entity_id: 'G1', retain: true, start: '2026-01-01T00:00:00', end: '2026-01-01T23:00:00',
      cost_basis: { currency: 'EUR', price_year: 2025, annual_fixed_cost_fraction: 0.1 } } }} />)
    const line = screen.getByTestId('card-meta').textContent ?? ''
    expect(line).toMatch(/^Checked against evidence · Retained run · G1 · .+ · 1 Jan 2026, 00:00–23:00 · €$/)
    expect(line).not.toContain('Not stated')
  })
})
