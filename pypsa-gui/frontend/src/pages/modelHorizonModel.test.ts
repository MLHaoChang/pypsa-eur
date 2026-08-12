// The Model Horizon weightings table addressed the wrong row on multi-period
// networks. `df_to_json(n.snapshot_weightings)` emits `period` / `timestep`
// columns for a MultiIndex — never `snapshot` or `name` — so the page's
// `wm.snapshot ?? wm.name ?? …` chain fell through to a bare ISO. The backend
// registers bare ISO keys once per period, last-write-wins, so a bare key
// resolves to the LAST period: editing 2030 wrote 2050.
import { describe, it, expect } from 'vitest'
import {
  snapshotWeightKey,
  buildWeightingRows,
  type WeightingRow,
} from './modelHorizonModel'

const FLAT_ROW: WeightingRow = {
  snapshot: '2024-01-01T00:00:00', objective: 1, generators: 1, stores: 1,
}
const MULTI_ROW: WeightingRow = {
  period: 2030, timestep: '2024-01-01T00:00:00', objective: 1, generators: 1, stores: 1,
}

describe('snapshotWeightKey', () => {
  it('returns the bare ISO on a flat network', () => {
    expect(snapshotWeightKey(FLAT_ROW, false, 'FALLBACK')).toBe('2024-01-01T00:00:00')
  })

  it('returns the period-qualified key on a multi-period network', () => {
    expect(snapshotWeightKey(MULTI_ROW, true, 'FALLBACK'))
      .toBe('2030|2024-01-01T00:00:00')
  })

  it('falls back to the positional ISO when the row carries neither shape', () => {
    expect(snapshotWeightKey({ objective: 1 }, false, '2024-06-01T12:00:00'))
      .toBe('2024-06-01T12:00:00')
  })

  it('does not emit a bare key on multi-period even if `snapshot` is also present', () => {
    const hybrid: WeightingRow = { ...MULTI_ROW, snapshot: '2024-01-01T00:00:00' }
    expect(snapshotWeightKey(hybrid, true, 'FALLBACK')).toBe('2030|2024-01-01T00:00:00')
  })
})

describe('buildWeightingRows', () => {
  it('produces distinct keys for the same hour under different periods', () => {
    // 3 periods x 24 hours = 72 rows on ONE page (PAGE_SIZE is 100). With the
    // old bare-ISO key this yielded 24 unique keys and 48 React collisions.
    const hours = Array.from({ length: 24 }, (_, h) =>
      `2024-01-01T${String(h).padStart(2, '0')}:00:00`)
    const periods = [2030, 2040, 2050]
    const pageRows: WeightingRow[] = periods.flatMap(p =>
      hours.map(ts => ({ period: p, timestep: ts, objective: 1, generators: 1, stores: 1 })))
    const allSnapshots = periods.flatMap(() => hours)

    const rows = buildWeightingRows(pageRows, allSnapshots, true, 0)

    expect(rows).toHaveLength(72)
    expect(new Set(rows.map(r => r.key)).size).toBe(72)
    expect(rows[0].key).toBe('2030|2024-01-01T00:00:00')
    expect(rows[24].key).toBe('2040|2024-01-01T00:00:00')
    expect(rows[48].key).toBe('2050|2024-01-01T00:00:00')
  })

  it('exposes the period for display on multi-period and null on flat', () => {
    expect(buildWeightingRows([MULTI_ROW], ['2024-01-01T00:00:00'], true, 0)[0].period)
      .toBe('2030')
    expect(buildWeightingRows([FLAT_ROW], ['2024-01-01T00:00:00'], false, 0)[0].period)
      .toBeNull()
  })

  it('reads the displayed timestamp from `timestep` on multi-period', () => {
    expect(buildWeightingRows([MULTI_ROW], ['IGNORED'], true, 0)[0].iso)
      .toBe('2024-01-01T00:00:00')
  })

  it('offsets into allSnapshots by pageStart when the row carries no timestamp', () => {
    const bare: WeightingRow = { objective: 3, generators: 1, stores: 1 }
    const rows = buildWeightingRows([bare], ['a', 'b', 'c'], false, 2)
    expect(rows[0].iso).toBe('c')
    expect(rows[0].key).toBe('c')
  })

  it('coerces weight columns to numbers and defaults missing ones to 1', () => {
    const partial: WeightingRow = { snapshot: 'x', objective: 2.5 }
    const [row] = buildWeightingRows([partial], ['x'], false, 0)
    expect(row.objective).toBe(2.5)
    expect(row.generators).toBe(1)
    expect(row.stores).toBe(1)
  })
})

import { resolutionLabel } from './modelHorizonModel'

describe('resolutionLabel', () => {
  it('names the known frequencies', () => {
    expect(resolutionLabel('h')).toBe('Hourly (h)')
    expect(resolutionLabel('3h')).toBe('3-hourly')
    expect(resolutionLabel('D')).toBe('Daily (D)')
    expect(resolutionLabel('MS')).toBe('Monthly (MS)')
  })

  it('matches case-insensitively — pandas may emit "H" rather than "h"', () => {
    expect(resolutionLabel('H')).toBe('Hourly (h)')
  })

  it('says irregular rather than guessing when the backend could not infer', () => {
    expect(resolutionLabel(null)).toBe('Irregular')
    expect(resolutionLabel(undefined)).toBe('Irregular')
  })

  it('passes an unrecognised alias through verbatim', () => {
    expect(resolutionLabel('17min')).toBe('17min')
  })
})

import { pvFactor } from './modelHorizonModel'

describe('pvFactor', () => {
  // Mirrors solver_service.py::_apply_modelling_assumptions step 4 exactly.
  // Any divergence here is the bug this replaced: the deleted manual
  // calculator omitted the `years` term and the inflation correction, so it
  // disagreed with what actually reached the LP by a factor of `years`.
  const base = { refPeriod: 2030, discountRate: 0.07, inflationRate: 0 }

  it('is 1 x years at the reference period', () => {
    expect(pvFactor({ ...base, period: 2030, years: 1 })).toBeCloseTo(1, 10)
    expect(pvFactor({ ...base, period: 2030, years: 10 })).toBeCloseTo(10, 10)
  })

  it('discounts later periods and multiplies by the period span', () => {
    // (1.07)^-10 = 0.508349...; x 10 years = 5.08349...
    expect(pvFactor({ ...base, period: 2040, years: 10 })).toBeCloseTo(5.083493, 5)
  })

  it('applies the Fisher correction when inflation is set', () => {
    // real r = 1.07/1.02 - 1 = 0.0490196...; (1+r)^-10 = 0.6196749505205055
    // (verified independently via `python3 -c "print((1+((1.07/1.02)-1))**-10)"`;
    // the brief's worked comment had 0.619669, off by ~6e-6 — enough to fail
    // toBeCloseTo(_, 5). Formula itself is unaffected and matches the backend
    // exactly; only this literal needed correcting.
    expect(pvFactor({ ...base, period: 2040, years: 1, inflationRate: 0.02 }))
      .toBeCloseTo(0.619675, 5)
  })

  it('clamps a pathological real rate at -0.999 so the base stays positive', () => {
    const v = pvFactor({ period: 2040, refPeriod: 2030, years: 1, discountRate: 0, inflationRate: 5000 })
    expect(Number.isFinite(v)).toBe(true)
    expect(v).toBeGreaterThan(0)
  })

  it('handles a zero discount rate as an identity on the PV term', () => {
    expect(pvFactor({ period: 2050, refPeriod: 2030, years: 5, discountRate: 0, inflationRate: 0 }))
      .toBeCloseTo(5, 10)
  })
})

import { horizonRangeLabel } from './modelHorizonModel'

describe('horizonRangeLabel', () => {
  it('shows the plain date span on a flat network', () => {
    expect(horizonRangeLabel(
      ['2024-01-01T00:00:00', '2024-06-01T00:00:00', '2024-12-31T23:00:00'],
      undefined, false,
    )).toBe('2024-01-01 → 2024-12-31')
  })

  it('leads with the investment periods on a multi-period network', () => {
    // Snapshots replicate ONE operational year under every period, so printing
    // the raw first/last timestep hides the horizon: a 2030/2040/2050 model
    // read as "2024-01-01 → 2024-12-31".
    expect(horizonRangeLabel(
      ['2024-01-01T00:00:00', '2024-12-31T23:00:00'],
      [2030, 2050], true,
    )).toBe('2030…2050 × op. 01-01→12-31')
  })

  it('handles a single investment period without a range arrow', () => {
    expect(horizonRangeLabel(
      ['2024-01-01T00:00:00', '2024-01-02T00:00:00'],
      [2030], true,
    )).toBe('2030 × op. 01-01→01-02')
  })

  it('degrades to a mode word when there are no snapshots', () => {
    expect(horizonRangeLabel([], [], false)).toBe('flat horizon')
    expect(horizonRangeLabel(undefined, undefined, true)).toBe('multi-period horizon')
  })

  it('handles a production-shaped PER-SNAPSHOT periods array without crashing or misreporting the span', () => {
    // ModelHorizon.tsx used to pass `snap.periods` here directly — the
    // PARALLEL per-snapshot array (`periods[i]` = the investment period of
    // `snapshots[i]`'s timestep), not the small investment-period list. On a
    // 3-period hourly model that's 3 × 8760 = 26,280 entries, not 3. The old
    // implementation computed `Math.min(...nums)` / `Math.max(...nums)` on
    // the full array via a spread — two full-array allocations plus a
    // spread-argument call, which both regresses input latency and risks
    // `RangeError: Maximum call stack size exceeded` as the array grows
    // (reachable around 15 periods × 8760 snapshots). The helper must stay
    // correct — and just as importantly, not need a smaller input to do so —
    // regardless of what shape of array a caller passes it.
    const periods: number[] = []
    for (const p of [2030, 2040, 2050]) {
      for (let i = 0; i < 8760; i++) periods.push(p)
    }
    expect(periods).toHaveLength(26280)
    const snapshots = ['2024-01-01T00:00:00', '2024-12-31T23:00:00']
    expect(horizonRangeLabel(snapshots, periods, true)).toBe('2030…2050 × op. 01-01→12-31')
  })

  it('does not throw RangeError: Maximum call stack size exceeded on a large per-snapshot periods array', () => {
    // The spread form (`Math.min(...nums)`) blows V8's argument-spread limit
    // once the array crosses roughly 100k-131k elements (verified directly:
    // `Math.min(...arr)` throws at 131,400 elements but not at 100,000 in this
    // Node build). 15 investment periods × 8760 hourly snapshots = 131,400 is
    // the finding's own worked example of where this becomes reachable — a
    // long but not outlandish multi-decade planning horizon. This must resolve
    // via a single pass, not a spread, regardless of array length.
    const periods: number[] = []
    const years = Array.from({ length: 15 }, (_, i) => 2030 + i * 10)
    for (const p of years) {
      for (let i = 0; i < 8760; i++) periods.push(p)
    }
    expect(periods).toHaveLength(131400)
    const snapshots = ['2024-01-01T00:00:00', '2024-12-31T23:00:00']
    expect(() => horizonRangeLabel(snapshots, periods, true)).not.toThrow()
    expect(horizonRangeLabel(snapshots, periods, true))
      .toBe(`${years[0]}…${years[years.length - 1]} × op. 01-01→12-31`)
  })
})

import {
  visibleSteps,
  isHorizonUnset,
  stepSummary,
  type HorizonStepId,
  type HorizonSummaryContext,
} from './modelHorizonModel'

describe('visibleSteps', () => {
  it('shows all six steps, in rail order, on a multi-period project', () => {
    expect(visibleSteps(true)).toEqual([
      'mode', 'years', 'economics', 'window', 'sampling', 'weights',
    ])
  })

  it('drops the three period-level steps on a single-period project', () => {
    expect(visibleSteps(false)).toEqual(['mode', 'window', 'sampling', 'weights'])
  })

  it('never includes a period-level step for single-period', () => {
    const periodSteps: HorizonStepId[] = ['years', 'economics']
    for (const id of periodSteps) {
      expect(visibleSteps(false)).not.toContain(id)
    }
  })
})

describe('isHorizonUnset', () => {
  it('is unset at 0 — no snapshots at all', () => {
    expect(isHorizonUnset(0)).toBe(true)
  })

  it('is unset at exactly 1 — PyPSA\'s default single "now" snapshot, not a configured horizon', () => {
    expect(isHorizonUnset(1)).toBe(true)
  })

  it('is configured at 2 — the smallest count that is not the default', () => {
    expect(isHorizonUnset(2)).toBe(false)
  })

  it('is unset when undefined — data has not loaded yet, never flash the summary early', () => {
    expect(isHorizonUnset(undefined)).toBe(true)
  })
})

describe('stepSummary', () => {
  const multiCtx: HorizonSummaryContext = {
    isMultiPeriod: true,
    periods: [2030, 2040, 2050],
    snapshotCount: 26280,
    freq: 'h',
    rangeLabel: '2030…2050 × op. 01-01→12-31',
    canSampleWeeks: true,
    weightsAreDefault: true,
  }
  const singleCtx: HorizonSummaryContext = {
    isMultiPeriod: false,
    periods: [],
    snapshotCount: 8760,
    freq: 'h',
    rangeLabel: '2024-01-01 → 2024-12-31',
    canSampleWeeks: false,
    weightsAreDefault: false,
  }

  describe('mode', () => {
    it('names the investment-year count on a multi-period project', () => {
      expect(stepSummary('mode', multiCtx)).toBe('Multi-period, 3 investment years')
    })

    it('says single-period, plainly, when not multi-period', () => {
      expect(stepSummary('mode', singleCtx)).toBe('Single-period')
    })

    it('singularises a lone investment year', () => {
      expect(stepSummary('mode', { ...multiCtx, periods: [2030] }))
        .toBe('Multi-period, 1 investment year')
    })
  })

  describe('years (multi-period only)', () => {
    it('lists the configured years, not just a count', () => {
      expect(stepSummary('years', multiCtx)).toBe('3 investment years: 2030, 2040, 2050')
    })

    it('says none are set when the list is empty, rather than "0 investment years: "', () => {
      expect(stepSummary('years', { ...multiCtx, periods: [] })).toBe('No investment years set')
    })
  })

  describe('economics (multi-period only)', () => {
    it('names every configured year, not just the endpoints', () => {
      expect(stepSummary('economics', multiCtx))
        .toBe('Objective weighting set across 3 years (2030…2040…2050)')
    })

    it('calls out that a single period needs no discounting', () => {
      expect(stepSummary('economics', { ...multiCtx, periods: [2030] }))
        .toBe('Single period (2030) — no discounting between periods')
    })

    it('has nothing to weight when no years are configured yet', () => {
      expect(stepSummary('economics', { ...multiCtx, periods: [] }))
        .toBe('No investment years to weight yet')
    })

    it('distinguishes two period sets with the same count and span but different spacing', () => {
      // [2030,2035,2050] and [2030,2040,2050] share count (3) and span (2030-2050) —
      // a summary keyed only on lo/hi would render both identically, hiding a real
      // difference: PyPSA's discount weighting is sensitive to inter-period spacing.
      const evenlySpaced = stepSummary('economics', { ...multiCtx, periods: [2030, 2040, 2050] })
      const unevenlySpaced = stepSummary('economics', { ...multiCtx, periods: [2030, 2035, 2050] })
      expect(evenlySpaced).not.toBe(unevenlySpaced)
      expect(unevenlySpaced).toBe('Objective weighting set across 3 years (2030…2035…2050)')
    })
  })

  describe('window', () => {
    it('composes the existing range label with the existing resolution label, multi-period', () => {
      expect(stepSummary('window', multiCtx)).toBe('2030…2050 × op. 01-01→12-31, Hourly (h)')
    })

    it('composes the existing range label with the existing resolution label, single-period', () => {
      expect(stepSummary('window', singleCtx)).toBe('2024-01-01 → 2024-12-31, Hourly (h)')
    })

    it('reflects a different resolution rather than always saying hourly', () => {
      expect(stepSummary('window', { ...singleCtx, freq: 'MS' }))
        .toBe('2024-01-01 → 2024-12-31, Monthly (MS)')
    })
  })

  describe('sampling', () => {
    it('names availability when the backend reports a full hourly year is present', () => {
      expect(stepSummary('sampling', multiCtx))
        .toBe('Not sampled — representative weeks available')
    })

    it('names the reason sampling is unavailable rather than repeating the available text', () => {
      expect(stepSummary('sampling', singleCtx))
        .toBe('Not sampled — upload an hourly profile to enable sampling')
    })

    it('does not claim a full year for a sub-year snapshot count', () => {
      // A single-period network can carry a custom sub-year window via the ordinary
      // Apply-snapshots path — entirely unrelated to representative-week sampling.
      // `ctx` has no field that supports a "this is a full year" claim, so the
      // sentence must not make one regardless of `canSampleWeeks`.
      const subYear: HorizonSummaryContext = { ...singleCtx, snapshotCount: 168, canSampleWeeks: true }
      expect(stepSummary('sampling', subYear)).not.toMatch(/full year/i)
      const subYearUnavailable: HorizonSummaryContext = { ...singleCtx, snapshotCount: 168 }
      expect(stepSummary('sampling', subYearUnavailable)).not.toMatch(/full year/i)
    })
  })

  describe('weights', () => {
    it('says default when every snapshot still carries weight 1', () => {
      expect(stepSummary('weights', multiCtx)).toBe('Default weights (every snapshot weighted 1)')
    })

    it('says custom when weights have been edited away from default', () => {
      expect(stepSummary('weights', singleCtx)).toBe('Custom weights applied')
    })
  })

  it('never returns the same sentence for genuinely different states, per step', () => {
    const pairs: Array<[HorizonStepId, HorizonSummaryContext, HorizonSummaryContext]> = [
      ['mode', multiCtx, singleCtx],
      ['years', multiCtx, { ...multiCtx, periods: [2030] }],
      ['economics', multiCtx, { ...multiCtx, periods: [2030] }],
      ['window', multiCtx, singleCtx],
      ['sampling', multiCtx, singleCtx],
      ['weights', multiCtx, singleCtx],
    ]
    for (const [step, a, b] of pairs) {
      expect(stepSummary(step, a)).not.toBe(stepSummary(step, b))
    }
  })
})
