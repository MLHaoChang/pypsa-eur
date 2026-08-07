import { describe, expect, it } from 'vitest'
import {
  isTruncatedPayload, weightedSum, snapshotWeightAt, effectiveWeightAt,
  type TSPayload, type WeightCtx, type SnapshotWeightRow,
} from './shared'
import {
  buildMultiPeriodWindowFixture, COLUMNS, WINDOW, TIMESTEPS_PER_PERIOD,
} from './__fixtures__/multiPeriodWindow'

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

  // Re-review finding (Important, NEW): `serialization.py::slice_ts` computes
  // `complete = (lo == 0 and hi == total - 1)` — i.e. `complete: false` on
  // EVERY legitimate window, capped or not. `filterContext.tsx`'s default
  // windows (first period on multi-period; rows 0-719 on flat >8760) are
  // ordinary, un-truncated requests that still carry `complete: false`. Only
  // `capped` distinguishes "the server truncated this" from "the user is
  // looking at a window they asked for". This case is what the previous
  // (`capped || complete === false`) predicate got backwards.
  it('is NOT truncated for an ordinary (uncapped) window — complete: false alone is not truncation', () => {
    const ts: TSPayload = {
      index: [], columns: [], data: [],
      range: { from: 0, to: 719, total: 26280, complete: false, capped: false },
    }
    expect(isTruncatedPayload([ts])).toBe(false)
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

// ── Windowed payload vs full-horizon weights: identity + cost ────────────────
// Real-payload scale (3 periods × 8,760 snapshots = 26,280 rows, 12 columns,
// weight rows shaped {period, timestep, objective, stores, generators}) with
// the window the repro used, `?from=17520&to=26279`.
//
// The two calls below must agree because they describe the SAME 8,760
// snapshots — one via a window-relative payload whose weight context still
// carries the full-horizon weightings array (what `useWeightCtx` actually
// builds: `snapshotWeights: snap?.weightings`, never sliced), the other via
// the whole-horizon payload restricted with `rowRange`.
//
// Production change under test: `_snapshotWeightRow` in shared.tsx. On a
// windowed payload its positional fast path (`sw[i]`) misses for essentially
// every row — `sw[0]` is period 2026's weight row while `iso`/`period` belong
// to period 2028 — so every row fell through to a linear `sw.find(...)` over
// 26,280 entries. 8,760 rows × 26,280 entries ≈ 2.3e8 predicate calls per
// call site.
describe('weightedSum: windowed payload vs full-horizon snapshot weights', () => {
  const fx = buildMultiPeriodWindowFixture()
  const cols = new Set(COLUMNS)

  // TEST 1 — the load-bearing identity.
  //
  // Pre-fix failure mode: the `sw.find(...)` fallback fires on all 8,760 rows,
  // and the windowed call alone measured ~5,853 ms — past vitest's 5,000 ms
  // default test timeout, so this case fails before the fix and passes after.
  // The numbers themselves match either way (the fallback resolves the RIGHT
  // row, just at O(n) each time), which is exactly why the regression was
  // invisible in the UI apart from the freeze.
  it('gives the same total for the window as for the same rows of the whole horizon', () => {
    const windowedTotal = weightedSum(fx.windowed, cols, fx.ctxWindow)
    const wholeSliceTotal = weightedSum(fx.whole, cols, fx.ctxWhole, WINDOW)
    expect(windowedTotal).not.toBeNull()
    expect(wholeSliceTotal).not.toBeNull()
    // Non-uniform weights, so this is a real number, not a degenerate 0.
    expect(Math.abs(windowedTotal as number)).toBeGreaterThan(0)
    expect(windowedTotal as number).toBeCloseTo(wholeSliceTotal as number, 9)
  })

  // Same identity on the 'objective' (cost) column — the other `column`
  // selection `weightedSum` supports, and the one Economics reads.
  it('holds for the objective column too', () => {
    const windowedTotal = weightedSum(fx.windowed, cols, fx.ctxWindow, undefined, 'objective')
    const wholeSliceTotal = weightedSum(fx.whole, cols, fx.ctxWhole, WINDOW, 'objective')
    expect(windowedTotal as number).toBeCloseTo(wholeSliceTotal as number, 9)
  })

  // TEST 2 — perf guard for the O(1) weight lookup.
  //
  // Measured before the fix: 5,853 ms for this single call (8,760 rows against
  // a 26,280-row weightings array). After replacing the `sw.find(...)`
  // fallback with a Map built once per `sw` array (memoised in a module-level
  // WeakMap), the same call is ~1e2 ms at worst on a loaded machine. The 500 ms
  // bound is ~12x the observed post-fix cost and ~1/12th of the pre-fix cost,
  // so it catches a reintroduced O(n^2) without being timing-flaky.
  it('resolves a windowed payload in well under 500 ms (no O(n^2) rescan)', () => {
    const t0 = performance.now()
    const total = weightedSum(fx.windowed, cols, fx.ctxWindow)
    const elapsed = performance.now() - t0
    expect(total).not.toBeNull()
    expect(elapsed).toBeLessThan(500)
  })

  // TEST 4 — no regression on the whole horizon.
  //
  // On a whole-horizon payload the positional fast path matches on every row,
  // so the fix must not touch this number at all. Pinned against an
  // independent positional recomputation rather than a magic constant.
  it('leaves whole-horizon totals unchanged (positional fast path still wins)', () => {
    const wholeTotal = weightedSum(fx.whole, cols, fx.ctxWhole)
    let expected = 0
    for (let row = 0; row < fx.whole.data.length; row++) {
      let s = 0
      for (let c = 0; c < COLUMNS.length; c++) s += fx.whole.data[row][c]
      const sw = fx.wholeWeights[row]
      const pw = fx.periodWeights.find(p => p.period === fx.whole.periods![row])
      expected += s * (sw.generators ?? 1) * (pw?.years ?? 1)
    }
    expect(wholeTotal as number).toBeCloseTo(expected, 6)
  })

  // Discrimination proof for the identity above: if the weight lookup fell
  // back to the window-relative POSITION (what `Dispatch.tsx`'s link-flow loop
  // did, and what a naive Map keyed only by timestep would do), the answer
  // would be a different number — period 2026's weights applied to period
  // 2028's rows. Pinning that here means the identity assertion cannot pass
  // by accident on all-uniform weights.
  it('is discriminating — the positional reading of the same window differs', () => {
    const windowedTotal = weightedSum(fx.windowed, cols, fx.ctxWindow) as number
    let positional = 0
    for (let row = 0; row < TIMESTEPS_PER_PERIOD; row++) {
      let s = 0
      for (let c = 0; c < COLUMNS.length; c++) s += fx.windowed.data[row][c]
      // sw[row] on a windowed payload = period 2026's row, the wrong basis.
      const sw = fx.wholeWeights[row]
      const pw = fx.periodWeights.find(p => p.period === fx.windowed.periods![row])
      positional += s * (sw.generators ?? 1) * (pw?.years ?? 1)
    }
    expect(Math.abs(windowedTotal - positional)).toBeGreaterThan(1)
  })
})

// ── snapshotWeightAt: FullLoadHoursSection's windowed-payload basis ─────────
// `Dispatch.tsx`'s `FullLoadHoursSection` (`perColumnPerPeriodEnergy`) has the
// SAME window-vs-full-horizon mismatch the tests above cover for
// `weightedSum`/`effectiveWeightAt`, at a site those two can't fix: it reads
// `weightCtx.snapshotWeights` (always full-horizon) by raw payload ROW index
// on a WINDOWED payload, and it deliberately must NOT apply
// `periodWeights.years` (full-load hours are per-solved-year hours, not an
// annualised figure — `effectiveWeightAt` always applies `years`, so it can't
// be reused here). `snapshotWeightAt` is the narrow export for this one site,
// sharing `_snapshotWeightRow`'s keyed lookup so there is still exactly one
// weight-resolution path.
describe('snapshotWeightAt: FullLoadHoursSection\'s windowed-payload basis', () => {
  const fx = buildMultiPeriodWindowFixture()
  const colIdx = 0 // COLUMNS[0]

  // Mirrors Dispatch.tsx's `perColumnPerPeriodEnergy` in 'positive' mode:
  // skip negative values, weight by `snapshotWeights.generators` ONLY.
  function flhEnergy(
    ts: TSPayload, ctx: WeightCtx, rFrom: number, rTo: number,
  ): number {
    const periods = ts.periods ?? ctx.snapshotPeriods ?? []
    let total = 0
    for (let row = rFrom; row <= rTo; row++) {
      const raw = ts.data[row][colIdx]
      if (raw < 0) continue
      const period = periods[row] ?? null
      total += raw * snapshotWeightAt(ctx, row, ts.index[row], period, 'generators')
    }
    return total
  }

  // TEST 1 — the load-bearing identity, same shape as FIX 3's TEST 1 above.
  it('gives the same FLH energy for the windowed payload as for the whole horizon sliced to the same rows', () => {
    const windowed = flhEnergy(fx.windowed, fx.ctxWindow, 0, fx.windowed.data.length - 1)
    const wholeSlice = flhEnergy(fx.whole, fx.ctxWhole, WINDOW.from, WINDOW.to)
    // Non-degenerate: real non-uniform weights, real (mostly-positive) data.
    expect(Math.abs(windowed)).toBeGreaterThan(0)
    expect(windowed).toBeCloseTo(wholeSlice, 6)
  })

  // TEST 2 — the `years` exclusion is load-bearing on its own; guard it so a
  // future "route this through effectiveWeightAt" tidy-up fails loudly.
  it('does NOT apply periodWeights.years — period 2028 (the windowed period) has years=3', () => {
    const noYears = flhEnergy(fx.windowed, fx.ctxWindow, 0, fx.windowed.data.length - 1)
    // Recompute the SAME sum but through `effectiveWeightAt`, which DOES fold
    // in `years` — the two must diverge by exactly that factor.
    let withYears = 0
    for (let row = 0; row < fx.windowed.data.length; row++) {
      const raw = fx.windowed.data[row][colIdx]
      if (raw < 0) continue
      withYears += raw * effectiveWeightAt(
        fx.ctxWindow, fx.windowed.index, row, 'generators', fx.windowed.index[row],
      )
    }
    expect(withYears).toBeCloseTo(noYears * 3, 6)
    expect(noYears).not.toBeCloseTo(withYears, 0)
  })
})
