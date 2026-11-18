/**
 * The checked-answer gate. A stream frame is published only after
 * `parseApproval` accepts its receipt and `verifyApprovedText` matches the
 * bytes the user would see. These cases are the boundaries that gate
 * enforces; the ChatPanel acceptance test covers one refused frame end to end.
 */
import { createHash, webcrypto } from 'node:crypto'
import { afterEach, describe, expect, it } from 'vitest'
import { parseApproval, verifyApprovedText } from './answerAssurance'

const HASH = 'a'.repeat(64)

function receipt(over: Record<string, unknown> = {}) {
  return {
    policy_version: '1',
    content_sha256: HASH,
    evidence_ids: ['evidence-1'],
    checked_block_ids: ['fact-1'],
    limitations: [],
    reviewer_status: 'not_required',
    ...over,
  }
}

function frame(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    version: 1,
    answer_id: 'answer-1',
    turn_id: 'turn-1',
    verdict: 'passed',
    receipt: receipt(),
    ...over,
  }
}

describe('parseApproval', () => {
  it('accepts a passed or limited receipt inside the size bounds', () => {
    const passed = parseApproval(frame())
    expect(passed?.verdict).toBe('passed')
    expect(passed?.receipt.content_sha256).toBe(HASH)

    const atBound = parseApproval(frame({
      answer_id: 'a'.repeat(128),
      turn_id: 't'.repeat(128),
      verdict: 'limited',
      receipt: receipt({
        reviewer_status: 'unavailable',
        evidence_ids: Array.from({ length: 20 }, (_, i) => `e${i}`),
        checked_block_ids: ['b'.repeat(4096)],
        limitations: ['L'.repeat(4096)],
      }),
    }))
    expect(atBound?.verdict).toBe('limited')
    expect(atBound?.receipt.evidence_ids).toHaveLength(20)
  })

  it.each([
    ['version 2', frame({ version: 2 })],
    ['string version', frame({ version: '1' })],
    ['empty answer id', frame({ answer_id: '' })],
    ['non-string answer id', frame({ answer_id: 1 })],
    ['answer id over 128', frame({ answer_id: 'a'.repeat(129) })],
    ['empty turn id', frame({ turn_id: '' })],
    ['turn id over 128', frame({ turn_id: 't'.repeat(129) })],
    ['unknown verdict', frame({ verdict: 'certified' })],
    ['missing receipt', frame({ receipt: null })],
    ['uppercase hash', frame({ receipt: receipt({ content_sha256: 'A'.repeat(64) }) })],
    ['short hash', frame({ receipt: receipt({ content_sha256: 'ab' }) })],
    ['wrong policy', frame({ receipt: receipt({ policy_version: '2' }) })],
    ['unknown reviewer', frame({ receipt: receipt({ reviewer_status: 'skipped' }) })],
    ['21 evidence ids', frame({ receipt: receipt({ evidence_ids: Array.from({ length: 21 }, (_, i) => `e${i}`) }) })],
    ['non-string limitation', frame({ receipt: receipt({ limitations: [{ text: 'x' }] }) })],
    ['limitation over 4096', frame({ receipt: receipt({ limitations: ['x'.repeat(4097)] }) })],
  ])('rejects %s', (_label, value) => {
    expect(parseApproval(value)).toBeNull()
  })
})

describe('receipt policy "2" (AA-03 S2 derived claims)', () => {
  const entry = { id: 'built', formula_id: 'difference/1', operands: ['a', 'b'], value: -13, unit: 'MW' }

  it('accepts policy "2" with one to eight derived entries', () => {
    const one = parseApproval(frame({ receipt: receipt({ policy_version: '2', derived: [entry] }) }))
    expect(one?.receipt.derived).toEqual([entry])
    const eight = Array.from({ length: 8 }, (_, i) => ({ ...entry, id: `d${i}` }))
    expect(parseApproval(frame({ receipt: receipt({ policy_version: '2', derived: eight }) }))).not.toBeNull()
  })

  it.each([
    ['policy "1" carrying derived', receipt({ derived: [entry] })],
    ['policy "2" without derived', receipt({ policy_version: '2' })],
    ['policy "2" with empty derived', receipt({ policy_version: '2', derived: [] })],
    ['nine derived entries', receipt({ policy_version: '2', derived: Array.from({ length: 9 }, (_, i) => ({ ...entry, id: `d${i}` })) })],
    ['an unknown key', receipt({ policy_version: '2', derived: [{ ...entry, expression: 'a - b' }] })],
    ['a missing key', receipt({ policy_version: '2', derived: [{ id: 'built', formula_id: 'difference/1', operands: ['a'], value: 1 }] })],
    ['a string value', receipt({ policy_version: '2', derived: [{ ...entry, value: '-13' }] })],
    ['a non-finite value', receipt({ policy_version: '2', derived: [{ ...entry, value: Infinity }] })],
    ['no operands', receipt({ policy_version: '2', derived: [{ ...entry, operands: [] }] })],
    ['a non-string operand', receipt({ policy_version: '2', derived: [{ ...entry, operands: [1] }] })],
    ['policy "3"', receipt({ policy_version: '3', derived: [entry] })],
  ])('rejects %s', (_label, value) => {
    expect(parseApproval(frame({ receipt: value }))).toBeNull()
  })
})

describe('receipt policy "3" (AA-03 S4 complete-population predicates)', () => {
  const common = { id: 'p1', evidence_id: 'E1', component_class: 'Load', metric: 'p_set', unit: 'MW', population: 3, warnings: ['w'] }
  const all = { ...common, quantifier: 'all', comparator: '>=', threshold: 5, holds: true }
  const top = { ...common, id: 'p2', quantifier: 'top_k', k: 1, members: ['L3'] }
  const derived = { id: 'd1', formula_id: 'difference/1', operands: ['a', 'b'], value: 1, unit: 'MW' }

  it('accepts policy "3" with predicates, with or without derived entries', () => {
    const parsed = parseApproval(frame({ receipt: receipt({ policy_version: '3', predicates: [all, top] }) }))
    expect(parsed?.receipt.predicates).toEqual([all, top])
    expect(parseApproval(frame({ receipt: receipt({ policy_version: '3', predicates: [top], derived: [derived] }) }))).not.toBeNull()
  })

  it.each([
    ['policy "1" carrying predicates', receipt({ predicates: [all] })],
    ['policy "2" carrying predicates', receipt({ policy_version: '2', derived: [derived], predicates: [all] })],
    ['policy "3" without predicates', receipt({ policy_version: '3' })],
    ['policy "3" with empty predicates', receipt({ policy_version: '3', predicates: [] })],
    ['policy "3" with empty derived', receipt({ policy_version: '3', predicates: [all], derived: [] })],
    ['policy "3" with null derived', receipt({ policy_version: '3', predicates: [all], derived: null })],
    ['nine predicates', receipt({ policy_version: '3', predicates: Array.from({ length: 9 }, (_, i) => ({ ...all, id: `p${i}` })) })],
    ['an unknown key', receipt({ policy_version: '3', predicates: [{ ...all, note: 'x' }] })],
    ['a numeric truth', receipt({ policy_version: '3', predicates: [{ ...all, holds: 1 }] })],
    ['a string threshold', receipt({ policy_version: '3', predicates: [{ ...all, threshold: '5' }] })],
    ['an unknown comparator', receipt({ policy_version: '3', predicates: [{ ...all, comparator: '==' }] })],
    ['an empty population', receipt({ policy_version: '3', predicates: [{ ...all, population: 0 }] })],
    ['top_k with a threshold', receipt({ policy_version: '3', predicates: [{ ...top, threshold: 5 }] })],
    ['k out of range', receipt({ policy_version: '3', predicates: [{ ...top, k: 21 }] })],
    ['an unknown quantifier', receipt({ policy_version: '3', predicates: [{ ...all, quantifier: 'some' }] })],
    ['policy "4" carrying predicates', receipt({ policy_version: '4', predicates: [all] })],
  ])('rejects %s', (_label, value) => {
    expect(parseApproval(frame({ receipt: value }))).toBeNull()
  })
})

describe('receipt policy "4" (issue 10 S6a sectioned answers)', () => {
  const slot = (n: number) => ({ metric: 'dispatch_energy', disposition: 'answered', valid_snapshots: 3, requested_snapshots: 3,
    entity_id: `G${n}`, component_class: 'Generator' })
  const full = Array.from({ length: 20 }, (_, i) => slot(i + 1))
  const sections = [{ heading: 'Generators 1–20 of 21', result_slots: full }, { heading: 'Generators 21 of 21', result_slots: [slot(21)] }]
  const sectioned = (over: Record<string, unknown> = {}) => receipt({ policy_version: '4', sections,
    checked_block_ids: Array.from({ length: 21 }, (_, i) => `b${i}`), ...over })

  it('accepts sections of up to twenty slots and up to a hundred checked blocks', () => {
    expect(parseApproval(frame({ receipt: sectioned() }))?.receipt.sections).toEqual(sections)
    const five = Array.from({ length: 5 }, (_, i) => ({ heading: `Generators ${i}`, result_slots: full }))
    const hundred = Array.from({ length: 100 }, (_, i) => `b${i}`)
    expect(parseApproval(frame({ receipt: sectioned({ sections: five, checked_block_ids: hundred }) }))).not.toBeNull()
    const lone = [{ heading: null, result_slots: [{ ...slot(1), disposition: 'unavailable', reason: 'invalid_or_missing_raw_data' }] }]
    expect(parseApproval(frame({ verdict: 'limited', receipt: sectioned({ sections: lone, checked_block_ids: [] }) }))).not.toBeNull()
  })

  it.each([
    ['policy "4" without sections', sectioned({ sections: undefined })],
    ['sections that are not a list', sectioned({ sections: { heading: null, result_slots: full } })],
    ['empty sections', sectioned({ sections: [] })],
    ['six sections', sectioned({ sections: Array.from({ length: 6 }, () => sections[1]) })],
    ['a section with no slots', sectioned({ sections: [{ heading: null, result_slots: [] }] })],
    ['a section over twenty slots', sectioned({ sections: [{ heading: null, result_slots: [...full, slot(21)] }] })],
    ['a section with an extra key', sectioned({ sections: [{ ...sections[1], note: 'x' }] })],
    ['a section with no heading key', sectioned({ sections: [{ result_slots: [slot(1)] }] })],
    ['a numeric heading', sectioned({ sections: [{ heading: 7, result_slots: [slot(1)] }] })],
    ['a slot with an extra key', sectioned({ sections: [{ heading: null, result_slots: [{ ...slot(1), fact: 1 }] }] })],
    ['a slot without its entity', sectioned({ sections: [{ heading: null, result_slots: [{ ...slot(1), entity_id: '' }] }] })],
    ['a clarification slot', sectioned({ sections: [{ heading: null, result_slots: [{ ...slot(1), disposition: 'needs_clarification' }] }] })],
    ['policy "4" with result_slots', sectioned({ result_slots: [slot(1)] })],
    ['policy "4" with derived entries', sectioned({ derived: [{ id: 'd1', formula_id: 'difference/1', operands: ['a'], value: 1, unit: 'MW' }] })],
    ['over a hundred checked blocks', sectioned({ checked_block_ids: Array.from({ length: 101 }, (_, i) => `b${i}`) })],
    ['policy "1" carrying sections', receipt({ sections })],
    ['policy "1" with twenty-one checked blocks', receipt({ checked_block_ids: Array.from({ length: 21 }, (_, i) => `b${i}`) })],
  ])('rejects %s', (_label, value) => {
    expect(parseApproval(frame({ receipt: value }))).toBeNull()
  })
})

describe('verifyApprovedText', () => {
  const previous = globalThis.crypto
  afterEach(() => {
    Object.defineProperty(globalThis, 'crypto', { value: previous, configurable: true })
  })

  it('matches the UTF-8 SHA-256 of the exact text and nothing else', async () => {
    useNodeCrypto()
    const text = 'café: p_set = 5 MW'
    const expected = createHash('sha256').update(text).digest('hex')
    const other = expected.slice(0, -1) + (expected.endsWith('0') ? '1' : '0')
    expect(await verifyApprovedText(text, expected)).toBe(true)
    expect(await verifyApprovedText(`${text} `, expected)).toBe(false)
    expect(await verifyApprovedText(text, other)).toBe(false)
  })

  it('refuses blank text, text over 64 KiB, and a missing digest', async () => {
    useNodeCrypto()
    const hash = createHash('sha256').update('x').digest('hex')
    expect(await verifyApprovedText('   ', hash)).toBe(false)
    expect(await verifyApprovedText('', hash)).toBe(false)
    const exact = 'y'.repeat(64 * 1024)
    const over = 'y'.repeat(64 * 1024 + 1)
    expect(await verifyApprovedText(exact, createHash('sha256').update(exact).digest('hex'))).toBe(true)
    expect(await verifyApprovedText(over, createHash('sha256').update(over).digest('hex'))).toBe(false)
    Object.defineProperty(globalThis, 'crypto', { value: undefined, configurable: true })
    expect(await verifyApprovedText('x', hash)).toBe(false)
  })
})

function useNodeCrypto() {
  Object.defineProperty(globalThis, 'crypto', {
    value: webcrypto as unknown as Crypto,
    configurable: true,
  })
}
