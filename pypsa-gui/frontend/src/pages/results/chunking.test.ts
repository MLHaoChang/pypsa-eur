import { describe, expect, it } from 'vitest'
import { chooseChunk, chunkBounds, CHUNK_STEPS } from './chunking'

describe('chooseChunk', () => {
  // Sizes derived from the measured ~10 bytes per serialised value against a
  // 512 KB target. 200 assets independently lands on a week, which is the
  // value a human would have picked — the other rows are why it is computed.
  it('picks a month for narrow networks', () => {
    expect(chooseChunk(20, 26280)).toBe(720)
    expect(chooseChunk(50, 26280)).toBe(720)
  })

  it('picks a week at 200 assets', () => {
    expect(chooseChunk(200, 26280)).toBe(168)
  })

  it('picks a day for wide networks', () => {
    expect(chooseChunk(2000, 26280)).toBe(24)
  })

  it('never goes below a day, even absurdly wide', () => {
    // A sub-day chunk would fetch a fragment of a diurnal cycle, which is the
    // unit every dispatch pattern in this domain is built on.
    expect(chooseChunk(100000, 26280)).toBe(CHUNK_STEPS[0])
    expect(chooseChunk(100000, 26280)).toBe(24)
  })

  it('never exceeds the horizon', () => {
    expect(chooseChunk(20, 100)).toBe(100)
    expect(chooseChunk(200, 10)).toBe(10)
  })

  it('falls back to a week when the asset count is unknown', () => {
    expect(chooseChunk(0, 26280)).toBe(168)
    expect(chooseChunk(-5, 26280)).toBe(168)
  })
})

describe('chunkBounds', () => {
  it('aligns to chunk boundaries so the cache key is stable', () => {
    // Every index inside one chunk must produce identical bounds, or React
    // Query refetches on every scrub step.
    const a = chunkBounds(168, 168, 26280)
    const b = chunkBounds(200, 168, 26280)
    const c = chunkBounds(335, 168, 26280)

    expect(a).toEqual({ from: 168, to: 335 })
    expect(b).toEqual(a)
    expect(c).toEqual(a)
  })

  it('produces adjacent non-overlapping chunks across a boundary', () => {
    const before = chunkBounds(335, 168, 26280)
    const after = chunkBounds(336, 168, 26280)

    expect(before.to + 1).toBe(after.from)
  })

  it('clamps the final chunk to the last row', () => {
    expect(chunkBounds(26279, 168, 26280)).toEqual({ from: 26208, to: 26279 })
  })

  it('respects a period clamp so a chunk cannot cross into another period', () => {
    // Multi-period: period 2 occupies rows 8760..17519. A chunk starting near
    // its end must stop at 17519, not spill into period 3.
    const bounds = chunkBounds(17500, 168, 26280, { start: 8760, end: 17519 })

    expect(bounds.to).toBe(17519)
    expect(bounds.from).toBeGreaterThanOrEqual(8760)
  })

  it('aligns relative to the clamp start, not to zero', () => {
    // Otherwise the first chunk of a period would be a short offcut.
    const bounds = chunkBounds(8760, 168, 26280, { start: 8760, end: 17519 })

    expect(bounds).toEqual({ from: 8760, to: 8927 })
  })
})
