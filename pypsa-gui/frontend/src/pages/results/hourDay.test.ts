// ui-modern 02 slice 3: binning and scale of the hour-by-day heatmap.
import { describe, expect, it } from 'vitest'
import { heatScale, heatStrength, hourDayGrid, spansPeriods } from './hourDay'

describe('hourDayGrid', () => {
  it('puts each snapshot in its calendar day and hour, for T and space stamps', () => {
    const g = hourDayGrid(['2030-07-01T00:00:00', '2030-07-01 13:00:00', '2030-07-02T23:00:00'], [5, 7, 9])
    expect(g.days).toEqual(['2030-07-01', '2030-07-02'])
    expect(g.cells[0][0]).toBe(5)
    expect(g.cells[0][13]).toBe(7)
    expect(g.cells[1][23]).toBe(9)
    expect(g.cells[0].length).toBe(24)
  })

  it('leaves an hour with no snapshot empty, never zero', () => {
    const g = hourDayGrid(['2030-07-01T00:00:00', '2030-07-01T02:00:00'], [5, null])
    expect(g.cells[0][1]).toBeNull()
    expect(g.cells[0][2]).toBeNull()
    expect(hourDayGrid(['2030-07-01T03:00:00'], [NaN]).days).toEqual([])
  })

  it('averages several snapshots in one hour', () => {
    const g = hourDayGrid(['2030-07-01T04:00:00', '2030-07-01T04:30:00'], [10, 20])
    expect(g.cells[0][4]).toBe(15)
  })

  it('skips a stamp it cannot read', () => {
    expect(hourDayGrid(['not a date', '2030-07-01T05:00:00'], [1, 2]).days).toEqual(['2030-07-01'])
  })

  it('covers a full year: 365 days of 24 hours', () => {
    const stamps: string[] = []
    const start = Date.UTC(2030, 0, 1)
    for (let h = 0; h < 8760; h++) stamps.push(new Date(start + h * 3600e3).toISOString().slice(0, 19))
    const g = hourDayGrid(stamps, stamps.map((_, i) => i))
    expect(g.days.length).toBe(365)
    expect(g.cells[364][23]).toBe(8759)
  })
})

describe('heatScale', () => {
  const a = hourDayGrid(['2030-07-01T00:00:00', '2030-07-01T01:00:00'], [40, 90])
  const b = hourDayGrid(['2030-07-01T00:00:00'], [120])

  it('is one scale over every map shown together', () => {
    expect(heatScale([a, b])).toEqual({ min: 40, max: 120 })
  })

  it('starts a quantity at zero, and keeps a negative low end', () => {
    expect(heatScale([a], true)).toEqual({ min: 0, max: 90 })
    const neg = hourDayGrid(['2030-07-01T00:00:00', '2030-07-01T01:00:00'], [-5, 30])
    expect(heatScale([neg], true)).toEqual({ min: -5, max: 30 })
  })

  it('is null when there is nothing to scale', () => {
    expect(heatScale([hourDayGrid([], [])])).toBeNull()
  })
})

describe('heatStrength', () => {
  it('runs from faint but visible at the low end to full at the high end', () => {
    const s = { min: 0, max: 200 }
    expect(heatStrength(0, s)).toBeCloseTo(0.08)
    expect(heatStrength(100, s)).toBeCloseTo(0.54)
    expect(heatStrength(200, s)).toBe(1)
    expect(heatStrength(500, s)).toBe(1)
    expect(heatStrength(-9, s)).toBeCloseTo(0.08)
  })

  it('draws a flat series at full strength', () => {
    expect(heatStrength(7, { min: 7, max: 7 })).toBe(1)
  })
})

describe('spansPeriods', () => {
  it('is true only when the rows in view cross an investment period', () => {
    expect(spansPeriods(undefined, 0, 9)).toBe(false)
    expect(spansPeriods([2030, 2030, 2040, 2040], 0, 3)).toBe(true)
    expect(spansPeriods([2030, 2030, 2040, 2040], 2, 3)).toBe(false)
    expect(spansPeriods([2030, 2030], 0, 99)).toBe(false)
  })
})
