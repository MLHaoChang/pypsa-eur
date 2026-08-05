import { describe, expect, it } from 'vitest'
import {
  isTruncatedPayload, weightedSum,
  type TSPayload, type WeightCtx, type SnapshotWeightRow,
} from './shared'

// ── isTruncatedPayload ───────────────────────────────────────────────────────
// The predicate the six windowing tabs (Dispatch, Curtailment, LostLoadTab,
// LoadFlow, Prices, StorageCycling) use to decide whether to show
// `WindowCapBanner` — the FIX 1 secondary fix from the results-tabs-window
// final review. Distinct from AggregatedOverview's `isPartialPayload`: that
// one guards a tab that must NEVER see a `range` key at all; this one is for
// tabs that legitimately request ranges and need to know when the server
// served LESS than what was asked for.
describe('isTruncatedPayload', () => {
  it('is false when no payload carries a range', () => {
    expect(isTruncatedPayload([{ index: [], columns: [], data: [] }])).toBe(false)
  })

  it('is false when the range is complete and not capped', () => {
    const ts: TSPayload = {
      index: [], columns: [], data: [],
      range: { from: 0, to: 719, total: 720, complete: true, capped: false },
    }
    expect(isTruncatedPayload([ts])).toBe(false)
  })

  it('is true when the range reports capped: true', () => {
    const ts: TSPayload = {
      index: [], columns: [], data: [],
      range: { from: 0, to: 227, total: 8760, complete: false, capped: true },
    }
    expect(isTruncatedPayload([ts])).toBe(true)
  })

  it('is true when the range reports complete: false even if capped is false', () => {
    const ts: TSPayload = {
      index: [], columns: [], data: [],
      range: { from: 0, to: 227, total: 8760, complete: false, capped: false },
    }
    expect(isTruncatedPayload([ts])).toBe(true)
  })

  it('is true if ANY payload in the array is truncated', () => {
    const whole: TSPayload = { index: [], columns: [], data: [] }
    const truncated: TSPayload = {
      index: [], columns: [], data: [],
      range: { from: 0, to: 0, total: 100, complete: false, capped: true },
    }
    expect(isTruncatedPayload([whole, null, truncated, undefined])).toBe(true)
  })

  it('ignores null and undefined payloads', () => {
    expect(isTruncatedPayload([null, undefined])).toBe(false)
  })
})

// ── weightedSum × _snapshotWeightRow (FIX 3) ─────────────────────────────────
// Reviewer-flagged Critical-adjacent bug: on a multi-period WINDOWED payload,
// `_snapshotWeightRow` used to match `sw[i]` positionally against
// `ts.index[row]` alone. PyPSA replicates one operational year across every
// investment period, so the same ISO string appears once per period in the
// full-horizon weightings array — a window that starts at a LATER period
// collides positionally with an EARLIER period's weight row, because the
// window-relative row index `i` (0, 1, 2, ...) doesn't line up with the
// absolute position the weightings array uses.
//
// This fixture reproduces exactly that: two periods (2026, 2027), each
// replicating the same two timesteps, with DIFFERENT `generators` weights
// per period (1.0 vs 3.0) — the only way to make a positional-vs-keyed
// mismatch produce a different NUMBER, not just a different code path.
describe('weightedSum on a multi-period windowed payload (FIX 3)', () => {
  const fullHorizonWeights: SnapshotWeightRow[] = [
    // period 2026 — rows 0-1 of the (hypothetical) full horizon
    { period: 2026, timestep: '2026-01-01T00:00:00', generators: 1.0 },
    { period: 2026, timestep: '2026-01-01T01:00:00', generators: 1.0 },
    // period 2027 — rows 2-3 of the full horizon. Same ISO strings as above
    // (PyPSA replicates the base operational year across periods) but a
    // DIFFERENT weight.
    { period: 2027, timestep: '2026-01-01T00:00:00', generators: 3.0 },
    { period: 2027, timestep: '2026-01-01T01:00:00', generators: 3.0 },
  ]

  // A WINDOW starting at period 2027 — row 0 of this payload is the window-
  // relative index, but it's period 2027's first row, not the full horizon's
  // row 0 (which belongs to period 2026).
  const windowedPayload: TSPayload = {
    index: ['2026-01-01T00:00:00', '2026-01-01T01:00:00'],
    columns: ['col'],
    data: [[10], [20]],
    periods: [2027, 2027],
  }

  const weightCtx: WeightCtx = {
    snapshots: undefined,
    snapshotPeriods: windowedPayload.periods,
    snapshotWeights: fullHorizonWeights,
    periodWeights: undefined,
  }

  it('uses each row\'s OWN period to look up its weight, not its window-relative position', () => {
    const sum = weightedSum(
      windowedPayload, new Set(['col']), weightCtx,
      { from: 0, to: 1 }, 'generators',
    )
    // Correct: both rows belong to period 2027 (weight 3.0), regardless of
    // their position in the window.
    //   (10 * 3.0) + (20 * 3.0) = 90
    // Observed pre-fix (positional `sw[i]` lookup): `i=0` collided with
    // `fullHorizonWeights[0]` (period 2026, weight 1.0) and `i=1` with
    // `fullHorizonWeights[1]` (period 2026, weight 1.0), giving
    //   (10 * 1.0) + (20 * 1.0) = 30
    // — period 1's weight silently applied to period 2's rows. This
    // assertion is the discrimination proof: reverting `_snapshotWeightRow`
    // to drop the period check (match on `timestep` alone) makes it fail,
    // because the sum would come back 30 instead of 90.
    expect(sum).toBe(90)
  })

  it('discriminates from the pre-fix positional-only behaviour (sanity check on the fixture)', () => {
    // Same fixture, but manually replicate what the OLD (period-blind)
    // `_snapshotWeightRow` would have returned, to make the discrimination
    // concrete rather than asserted only via the comment above.
    const buggyPositionalSum = windowedPayload.data.reduce((acc, row, i) => {
      const buggyRow = fullHorizonWeights[i] // positional, no period check
      return acc + row[0] * (buggyRow.generators ?? 1)
    }, 0)
    expect(buggyPositionalSum).toBe(30)
    const fixedSum = weightedSum(
      windowedPayload, new Set(['col']), weightCtx,
      { from: 0, to: 1 }, 'generators',
    )
    expect(fixedSum).not.toBe(buggyPositionalSum)
    expect(fixedSum).toBe(90)
  })
})
