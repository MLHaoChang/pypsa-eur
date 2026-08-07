// Shared fixture: a 3-period × 8,760-snapshot network served as a WINDOWED
// payload, against FULL-HORIZON snapshot weights.
//
// This is the exact shape the Results tabs see in production after the
// windowing conversion:
//
//   • `/api/results/*` is fetched with `?from=17520&to=26279`, so the payload's
//     `data`/`index`/`periods` are WINDOW-RELATIVE — row 0 is global snapshot
//     17,520 (the first hour of the third investment period).
//   • `/api/network/snapshots` is NOT windowed, so `weightings` is always the
//     full 26,280-row array. `useWeightCtx` (shared.tsx) hands that array
//     straight into `WeightCtx.snapshotWeights`.
//
// Consequence: any code that indexes `snapshotWeights` with a payload row
// index reads the wrong snapshot's weight. PyPSA replicates ONE operational
// year across every investment period, so the ISO timestep strings repeat
// verbatim per period — position is the only thing that differs, which is
// precisely why a positional lookup fails silently instead of loudly.
//
// The weights here are deliberately NON-UNIFORM and differ per period for the
// same timestep. All-1.0 weights make every alignment bug invisible.
//
// Shared by `shared.test.ts` (weightedSum identity + perf guard) and
// `Dispatch.test.tsx` (link-flow totals) so the two cannot drift apart — the
// drift between those two call sites is what produced the bug in the first
// place.

import type {
  TSPayload, WeightCtx, SnapshotWeightRow, InvestmentPeriodWeightRow,
} from '../shared'

export const PERIODS = [2026, 2027, 2028] as const
export const TIMESTEPS_PER_PERIOD = 8760
export const TOTAL_ROWS = PERIODS.length * TIMESTEPS_PER_PERIOD // 26,280

/** The window the repro used: the whole of the THIRD period (2028). */
export const WINDOW = { from: 17520, to: 26279 }

/** 12 columns — matches the repro's column count. */
export const COLUMNS = Array.from({ length: 12 }, (_, i) => `asset_${i}`)

// One operational year of hourly ISO strings in the BASE year (2026). PyPSA
// replicates this same array under every investment period, so `index` is
// these 8,760 strings repeated three times.
function baseYearTimesteps(): string[] {
  const out: string[] = []
  const start = Date.UTC(2026, 0, 1, 0, 0, 0)
  for (let h = 0; h < TIMESTEPS_PER_PERIOD; h++) {
    out.push(new Date(start + h * 3_600_000).toISOString().slice(0, 19))
  }
  return out
}

// Deterministic, non-uniform, and a function of the GLOBAL row index `g` — so
// the same timestep carries a different weight in each period. Any lookup that
// resolves by position instead of by (period, timestep) lands on a different
// number, which is what makes the assertions discriminating.
function weightsFor(g: number): { generators: number; objective: number; stores: number } {
  return {
    generators: 1 + ((g * 7) % 13) / 4,
    objective: 1 + ((g * 11) % 17) / 5,
    stores: 1 + ((g * 5) % 11) / 3,
  }
}

export interface MultiPeriodWindowFixture {
  /** Full-horizon payload: 26,280 rows. */
  whole: TSPayload
  /** Window-relative payload: rows 17,520–26,279 re-based to 0–8,759. */
  windowed: TSPayload
  /** Full-horizon snapshot weightings, exactly as `/network/snapshots` serves. */
  wholeWeights: SnapshotWeightRow[]
  periodWeights: InvestmentPeriodWeightRow[]
  /** Weight context a WHOLE-horizon payload gets. */
  ctxWhole: WeightCtx
  /** Weight context a WINDOWED payload gets — note the FULL-horizon weights. */
  ctxWindow: WeightCtx
}

export function buildMultiPeriodWindowFixture(): MultiPeriodWindowFixture {
  const timesteps = baseYearTimesteps()

  const index: string[] = []
  const periods: number[] = []
  const data: number[][] = []
  const wholeWeights: SnapshotWeightRow[] = []

  for (let p = 0; p < PERIODS.length; p++) {
    const period = PERIODS[p]
    for (let t = 0; t < TIMESTEPS_PER_PERIOD; t++) {
      const g = p * TIMESTEPS_PER_PERIOD + t
      index.push(timesteps[t])
      periods.push(period)
      const row = new Array<number>(COLUMNS.length)
      for (let c = 0; c < COLUMNS.length; c++) {
        // Mixed sign so positive/negative split paths are exercised too.
        row[c] = ((g % 97) + c * 3.5) - 40
      }
      data.push(row)
      // Row shape mirrors the real payload: {period, timestep, objective,
      // stores, generators} — no `snapshot` key on multi-period networks.
      wholeWeights.push({ period, timestep: timesteps[t], ...weightsFor(g) })
    }
  }

  const whole: TSPayload = { index, columns: COLUMNS, data, periods }

  const windowed: TSPayload = {
    index: index.slice(WINDOW.from, WINDOW.to + 1),
    columns: COLUMNS,
    data: data.slice(WINDOW.from, WINDOW.to + 1),
    periods: periods.slice(WINDOW.from, WINDOW.to + 1),
    range: {
      from: WINDOW.from, to: WINDOW.to, total: TOTAL_ROWS,
      complete: false, capped: false,
    },
  }

  const periodWeights: InvestmentPeriodWeightRow[] = [
    { period: 2026, years: 1, objective: 1 },
    { period: 2027, years: 5, objective: 4.2 },
    { period: 2028, years: 3, objective: 2.7 },
  ]

  // Mirrors `useWeightCtx`: `snapshots` and `snapshotWeights` always come from
  // /network/snapshots (full horizon); only `snapshotPeriods` follows the
  // payload, because it prefers `refTs.periods`.
  const ctxWhole: WeightCtx = {
    snapshots: index,
    snapshotPeriods: whole.periods,
    snapshotWeights: wholeWeights,
    periodWeights,
  }
  const ctxWindow: WeightCtx = {
    snapshots: index,
    snapshotPeriods: windowed.periods,
    snapshotWeights: wholeWeights,
    periodWeights,
  }

  return { whole, windowed, wholeWeights, periodWeights, ctxWhole, ctxWindow }
}
