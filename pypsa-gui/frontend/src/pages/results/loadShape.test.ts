import { describe, expect, it } from 'vitest'
import type { AnnualRow, HeatmapState, MonthRow, WeekView } from '../../api/loadShape'
import {
  annualStack, cellTitle, fmtMwh, heatBackground, heatColor, NO_SNAPSHOT_HATCH, heatMax, missingGroups, monthRows, socRows, weekRows,
} from './loadShape'

const HEAT: HeatmapState = {
  status: 'ok',
  mean_mw: Array.from({ length: 7 }, (_, d) => Array.from({ length: 24 }, (_, h) => (d === 0 && h === 0 ? 10 : d === 0 && h === 1 ? 0 : d === 0 && h === 2 ? null : 5))),
  snapshots: Array.from({ length: 7 }, () => Array(24).fill(1)),
}

export const WEEK: Extract<WeekView, { status: 'ok' }> = {
  status: 'ok', selected: 0, import_capacity_mw: 40,
  weeks: [{ key: '2030-W10', from: 0, to: 3, start: '2030-03-04 00:00:00', end: '2030-03-04 03:00:00' }],
  index: ['t0', 't1', 't2', 't3'],
  series: {
    grid: { status: 'ok', mw: [1, 2, 3, 4] }, export: { status: 'ok', mw: [0, 0, 1, 0] },
    solar: { status: 'ok', mw: [0, 2, 4, 1] }, wind: { status: 'not_modelled', reason: 'no wind' },
    genset: { status: 'not_modelled', reason: 'no gensets' }, ev: { status: 'not_modelled', reason: 'no EV' },
    flexible: { status: 'not_modelled', reason: 'no flex' }, battery_soc: { status: 'ok', mw: [1, 1, 2, 2] },
    unserved: { status: 'unavailable', reason: 'no lost-load capture' },
  },
  flagged: [2], flag_basis: ['grid import at 40 MW connection'],
}

describe('heatmap colours', () => {
  it('shades the peak darkest, a computed zero lightest, and a missing cell not at all', () => {
    expect(heatMax(HEAT)).toBe(10)
    const peak = heatColor(10, 10), zero = heatColor(0, 10), none = heatColor(null, 10)
    expect(none).toBe('transparent')
    expect(zero).not.toBe(none)                       // a zero is data, a gap is not
    expect(peak).toContain('0.900')
    expect(zero).toContain('0.050')
  })
  it('hatches a cell with no snapshot, so it never reads as the lightest shade', () => {
    expect(heatBackground(null, 10)).toBe(NO_SNAPSHOT_HATCH)
    expect(heatBackground(0, 10)).toBe(heatColor(0, 10))          // a computed zero is a shade, not a hatch
    expect(heatBackground(0, 10)).not.toContain('repeating')
    expect(NO_SNAPSHOT_HATCH).toContain('repeating-linear-gradient')
  })
  it('names the cell and its sample count in the hover title', () => {
    expect(cellTitle(0, 7, 12.34, 3)).toBe('Mon 07:00: 12.3 MW (3 snapshots)')
    expect(cellTitle(1, 0, null, 0)).toBe('Tue 00:00: no snapshot')
  })
  it('has no peak for a heatmap the server did not draw', () => {
    expect(heatMax({ status: 'not_modelled', reason: 'x' })).toBe(0)
  })
})

describe('week rows', () => {
  it('keeps only the groups with data and marks the flagged interval at the top of the axis', () => {
    const { rows, keys, top } = weekRows(WEEK)
    expect(keys).toEqual(['grid', 'export', 'solar'])
    expect(top).toBe(4)
    expect(rows[2]).toMatchObject({ t: 't2', grid: 3, solar: 4, flag: 4 })
    expect(rows[1].flag).toBeUndefined()
    expect(rows.every(r => !('wind' in r) && !('unserved' in r))).toBe(true)     // never a zero line
  })
  it('gives the battery state of charge only when there is a battery', () => {
    expect(socRows(WEEK)).toEqual([{ t: 't0', soc: 1 }, { t: 't1', soc: 1 }, { t: 't2', soc: 2 }, { t: 't3', soc: 2 }])
    expect(socRows({ ...WEEK, series: { ...WEEK.series, battery_soc: { status: 'not_modelled', reason: 'x' } } })).toBeNull()
  })
  it('lists what could not be drawn with the server reason', () => {
    const gone = missingGroups({ grid: { status: 'ok' }, wind: { status: 'not_modelled', reason: 'no wind' },
                                 unserved: { status: 'unavailable', reason: 'no capture' } })
    expect(gone.map(g => [g.key, g.kind, g.reason])).toEqual([
      ['wind', 'not_modelled', 'no wind'], ['unserved', 'unavailable', 'no capture']])
  })
})

describe('monthly and annual', () => {
  const months: MonthRow[] = [
    { month: '2030-01', solar: 10, wind: null, genset: null, grid: 20, export: 0, excess: null, daily_generation_mwh: 1 },
    { month: '2030-02', solar: 12, wind: null, genset: null, grid: 18, export: 2, excess: 3, daily_generation_mwh: 1.5 },
  ]
  it('leaves a group the server did not establish out of the row, keeps a computed zero', () => {
    const rows = monthRows(months)
    expect(rows[0]).toEqual({ month: '2030-01', solar: 10, grid: 20, export: 0, daily: 1 })
    expect('wind' in rows[0]).toBe(false)
    expect(rows[1].excess).toBe(3)
  })
  const row: Extract<AnnualRow, { status: 'ok' }> = {
    status: 'ok', period: 2030, load_served: 90, solar: 30, wind: null, genset: 10, grid: 60, export: 5, excess: null,
  }
  it('stacks the supply, names what is not modelled and keeps the rest beside it', () => {
    const st = annualStack(row)
    expect(st.segments.map(s => s.key)).toEqual(['solar', 'genset', 'grid'])
    expect(st.total).toBe(100)
    expect(st.notModelled).toEqual(['Wind'])
    expect(st.export).toBe(5)
    expect(st.excess).toBeNull()
  })
  it('formats energy with a unit prefix', () => {
    expect(fmtMwh(12_000)).toMatch(/GWh$/)
    expect(fmtMwh(null)).toBe('not established')
  })
})
