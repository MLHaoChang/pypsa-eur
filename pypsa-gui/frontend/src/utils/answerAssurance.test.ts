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
