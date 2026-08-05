import { describe, expect, it } from 'vitest'
import { chooseChunk, chunkBounds, horizonOf, localRow, CHUNK_STEPS } from './chunking'
import type { TSPayload } from './shared'

// Minimal TSPayload builder for horizonOf/localRow tests — only the fields
// those two functions read (`data.length`, `range.total`, `range.from`)
// need real values; `columns`/`index` are irrelevant filler.
function mkPayload(opts: { rows: number; range?: { from: number; to: number; total: number } }): TSPayload {
  return {
    index: Array.from({ length: opts.rows }, (_, i) => String(i)),
    columns: ['A0'],
    data: Array.from({ length: opts.rows }, () => [0]),
    ...(opts.range ? { range: { ...opts.range, complete: false, capped: false } } : {}),
  }
}

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

  it('aligns relative to the clamp start one chunk in, where zero-relative would differ', () => {
    // The earlier clamp-start test uses idx === clampTo.start, where
    // Math.max(lo, ...) masks a zero-relative offset. This index sits one
    // full chunk past the period start, so the two formulas diverge:
    // lo-relative -> 8928, zero-relative -> 8904.
    expect(chunkBounds(8928, 168, 26280, { start: 8760, end: 17519 }))
      .toEqual({ from: 8928, to: 9095 })
  })

  it('maps a global snapshot index into its chunk-local row', () => {
    // Snapshot 200 lives in chunk [168, 335] at local row 32.
    const bounds = chunkBounds(200, 168, 26280)
    const local = 200 - bounds.from

    expect(local).toBe(32)
    expect(bounds.from + local).toBe(200)
  })
})

describe('horizonOf', () => {
  // This is the assertion that catches the original defect: clamping a
  // snapshot index against a CHUNK's length instead of the series' true
  // horizon silently renders the wrong row's flows under the right label.
  it('returns the HORIZON when a payload carries range.total, not that payload\'s data.length', () => {
    // A ranged response: the chunk actually fetched is 168 rows, but the
    // series' true horizon (range.total) is 26280.
    const chunked = mkPayload({ rows: 168, range: { from: 8592, to: 8759, total: 26280 } })

    expect(horizonOf([chunked])).toBe(26280)
    expect(horizonOf([chunked])).not.toBe(chunked.data.length)
  })

  it('falls back to data.length for an unranged payload', () => {
    // Pre-range, unconverted shape: no `range` block at all, so data.length
    // IS the whole series.
    const unranged = mkPayload({ rows: 720 })

    expect(horizonOf([unranged])).toBe(720)
  })

  it('prefers the first payload carrying range.total over later payloads', () => {
    const first = mkPayload({ rows: 24, range: { from: 0, to: 23, total: 8760 } })
    const second = mkPayload({ rows: 24, range: { from: 0, to: 23, total: 100 } })

    expect(horizonOf([first, second])).toBe(8760)
  })

  it('returns 0 for all-null / all-empty input', () => {
    expect(horizonOf([null, undefined])).toBe(0)
    expect(horizonOf([mkPayload({ rows: 0 })])).toBe(0)
    expect(horizonOf([])).toBe(0)
  })
})

describe('localRow', () => {
  it('offsets by the payload\'s own range.from', () => {
    const payload = mkPayload({ rows: 168, range: { from: 8592, to: 8759, total: 26280 } })

    expect(localRow(payload, 8600)).toBe(8)
  })

  it('returns the raw index for an unranged payload', () => {
    const payload = mkPayload({ rows: 720 })

    expect(localRow(payload, 42)).toBe(42)
  })

  it('returns the raw index for a null/undefined payload', () => {
    expect(localRow(null, 42)).toBe(42)
    expect(localRow(undefined, 42)).toBe(42)
  })
})
