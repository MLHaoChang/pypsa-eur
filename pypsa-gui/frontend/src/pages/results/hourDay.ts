// The numbers behind the hour-by-day heatmap (ui-modern 02, slice 3): a series
// of snapshots binned into calendar day × hour of day, and the scale that
// turns a value into colour strength. Kept apart from the component so the
// maps shown together can share one scale.
import { parseSeasonStamp } from './shared'

export interface HourDayGrid {
  /** Calendar days in the window, in order: "2030-07-01". */
  days: string[]
  /** cells[day][hour 0..23]: the mean of that hour's snapshots, or null when it has none. */
  cells: Array<Array<number | null>>
}

/**
 * Bin `values` (parallel to `stamps`) by calendar day and hour of day. Several
 * snapshots in one hour (a sub-hourly run) are averaged; a value that is
 * missing or not finite is no snapshot, never a zero.
 */
export function hourDayGrid(stamps: readonly string[], values: ReadonlyArray<number | null | undefined>): HourDayGrid {
  const dayAt = new Map<string, number>()
  const sums: number[][] = []
  const counts: number[][] = []
  stamps.forEach((stamp, i) => {
    const v = values[i]
    const at = parseSeasonStamp(stamp)
    if (!at || v == null || !Number.isFinite(v)) return
    const day = stamp.slice(0, 10)
    let d = dayAt.get(day)
    if (d == null) {
      d = sums.length
      dayAt.set(day, d)
      sums.push(new Array<number>(24).fill(0))
      counts.push(new Array<number>(24).fill(0))
    }
    sums[d][at.hour] += v
    counts[d][at.hour] += 1
  })
  return {
    days: [...dayAt.keys()],
    cells: sums.map((row, d) => row.map((s, h) => (counts[d][h] ? s / counts[d][h] : null))),
  }
}

/** Why an all-periods view has no hour-by-day map. */
export const PICK_ONE_PERIOD = 'Pick one period to see hours by day: periods share calendar dates.'

export interface HeatScale { min: number; max: number }

/**
 * One scale over every grid given: maps shown together pass the same scale, so
 * the same colour means the same value in each. `fromZero` anchors the low end
 * at zero (a quantity such as curtailed power); without it the scale runs from
 * the lowest value (a price). Null when no grid has a value.
 */
export function heatScale(grids: readonly HourDayGrid[], fromZero = false): HeatScale | null {
  let min = Infinity
  let max = -Infinity
  for (const g of grids) for (const row of g.cells) for (const v of row) {
    if (v == null) continue
    if (v < min) min = v
    if (v > max) max = v
  }
  if (max === -Infinity) return null
  return { min: fromZero ? Math.min(0, min) : min, max }
}

/** Colour strength of a value on a scale: 0.08 at the low end (still visible on the surface) to 1 at the high end. */
export function heatStrength(v: number, scale: HeatScale): number {
  const span = scale.max - scale.min
  const t = span > 0 ? Math.min(1, Math.max(0, (v - scale.min) / span)) : 1
  return 0.08 + 0.92 * t
}

/** True when the rows in view belong to more than one investment period: their calendar dates collide. */
export function spansPeriods(periods: ReadonlyArray<number | string> | undefined, from: number, to: number): boolean {
  if (!periods || periods.length === 0) return false
  const first = periods[Math.max(0, from)]
  for (let i = Math.max(0, from) + 1; i <= Math.min(periods.length - 1, to); i++) {
    if (periods[i] !== first) return true
  }
  return false
}
