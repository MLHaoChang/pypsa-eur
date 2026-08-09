import { describe, expect, it } from 'vitest'
import {
  deriveCostEur,
  CACHE_READ_MULTIPLIER,
  CACHE_WRITE_MULTIPLIER,
  PRICING_USD_PER_MTOK,
  USD_PER_EUR,
  type ChatUsageAcc,
} from './chatStore'

// Improvement #12 — the cost meter has to be worth trusting.
//
// Two defects, both silent. The meter reported a number all along; it was
// simply the wrong number, and nothing about the display said so.
//
//   * Cached tokens were free. `deriveCostEur` summed input + output only,
//     while the store tracked cache_read/cache_create and threaded them
//     through every turn. Prompt caching is on for the system prompt, the
//     tool array AND the conversation history, so on a long session the
//     untracked tokens are most of the bill.
//
//   * Opus was priced at 3x its real rate. $15/$75 per MTOK is Opus 4.1-era
//     pricing; claude-opus-4-8 is $5/$25. A user comparing models in the
//     header was told Opus costs five times Sonnet when it costs under two.

function usage(over: Partial<ChatUsageAcc> = {}): ChatUsageAcc {
  return {
    input_tokens: 0,
    output_tokens: 0,
    cache_read_tokens: 0,
    cache_create_tokens: 0,
    ...over,
  }
}

const eur = (usd: number) => usd / USD_PER_EUR

describe('deriveCostEur', () => {
  it('bills cache reads at a tenth of the input rate', () => {
    const sonnet = PRICING_USD_PER_MTOK['claude-sonnet-4-6']

    const cost = deriveCostEur('claude-sonnet-4-6', usage({
      cache_read_tokens: 1_000_000,
    }))

    // 1M cached-read tokens at 0.1 x $3.00.
    expect(cost).toBeCloseTo(eur(0.1 * sonnet.input), 6)
  })

  it('bills cache writes above the input rate, not below it', () => {
    const sonnet = PRICING_USD_PER_MTOK['claude-sonnet-4-6']

    const write = deriveCostEur('claude-sonnet-4-6', usage({
      cache_create_tokens: 1_000_000,
    }))
    const plain = deriveCostEur('claude-sonnet-4-6', usage({
      input_tokens: 1_000_000,
    }))

    // Writing the cache costs a 25% premium over sending the tokens
    // uncached; reading it back is what pays that premium off. A formula
    // that treated a write as a discount would invert the whole point.
    expect(write).toBeGreaterThan(plain)
    expect(write).toBeCloseTo(eur(1.25 * sonnet.input), 6)
  })

  it('adds every token class into one total', () => {
    const p = PRICING_USD_PER_MTOK['claude-sonnet-4-6']
    const u = usage({
      input_tokens: 10_000,
      output_tokens: 2_000,
      cache_read_tokens: 500_000,
      cache_create_tokens: 40_000,
    })

    const expectedUsd = (
      10_000 * p.input
      + 2_000 * p.output
      + 500_000 * p.input * CACHE_READ_MULTIPLIER
      + 40_000 * p.input * CACHE_WRITE_MULTIPLIER
    ) / 1_000_000

    expect(deriveCostEur('claude-sonnet-4-6', u)).toBeCloseTo(eur(expectedUsd), 6)
  })

  it('still returns zero for a session that has spent nothing', () => {
    expect(deriveCostEur('claude-sonnet-4-6', usage())).toBe(0)
  })

  it('falls back to the default model price for an unknown model', () => {
    const cost = deriveCostEur(
      'claude-something-unreleased' as never,
      usage({ input_tokens: 1_000_000 }),
    )
    expect(cost).toBeCloseTo(
      eur(PRICING_USD_PER_MTOK['claude-sonnet-4-6'].input), 6,
    )
  })
})

describe('PRICING_USD_PER_MTOK', () => {
  it('prices Opus 4.8 at its actual rate, not Opus 4.1-era rates', () => {
    // claude-opus-4-8 is $5 / $25 per MTOK. The table said $15 / $75 —
    // Opus 4.1 pricing — so every Opus turn was reported at 3x cost.
    expect(PRICING_USD_PER_MTOK['claude-opus-4-8']).toEqual({
      input: 5.0,
      output: 25.0,
    })
  })

  it('keeps Sonnet 4.6 at $3 / $15', () => {
    expect(PRICING_USD_PER_MTOK['claude-sonnet-4-6']).toEqual({
      input: 3.0,
      output: 15.0,
    })
  })

  it('makes Opus dearer than Sonnet, but not five times dearer', () => {
    // Guards the direction of the fix: the old table made Opus look 5x
    // Sonnet on input, which is the comparison the model picker exists to
    // inform.
    const opus = PRICING_USD_PER_MTOK['claude-opus-4-8']
    const sonnet = PRICING_USD_PER_MTOK['claude-sonnet-4-6']
    expect(opus.input).toBeGreaterThan(sonnet.input)
    expect(opus.input / sonnet.input).toBeLessThan(2)
  })
})
