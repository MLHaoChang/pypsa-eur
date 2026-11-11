import { expect, it } from 'vitest'
import { timeAxis } from './shared'

it('leaves a short series on the default tick layout', () => {
  expect(timeAxis(24)).toEqual({})
  expect(timeAxis(200)).toEqual({})
})

it('gives a full year a numeric interval, so no label is measured', () => {
  const { interval } = timeAxis(8760)
  expect(typeof interval).toBe('number')
  // ~8 ticks across the year: every (interval + 1)th of 8760 stamps.
  const ticks = Math.ceil(8760 / ((interval as number) + 1))
  expect(ticks).toBeGreaterThanOrEqual(6)
  expect(ticks).toBeLessThanOrEqual(10)
})

import { MAX_CHART_POINTS, thinRows } from './shared'

const year = Array.from({ length: 8760 }, (_, i) => ({
  snapshot: `t${i}`, a: i % 24, b: i === 4321 ? 900 : 1,
}))

it('thinRows leaves a short series untouched (same array)', () => {
  const rows = year.slice(0, 100)
  expect(thinRows(rows)).toBe(rows)
})

it('thinRows draws a full year in at most the point budget, whole rows, in order', () => {
  const out = thinRows(year)
  expect(out.length).toBeLessThanOrEqual(MAX_CHART_POINTS + 2)
  expect(out[0]).toBe(year[0])
  expect(out[out.length - 1]).toBe(year[8759])
  const idx = out.map(r => Number(r.snapshot.slice(1)))
  expect(idx).toEqual([...idx].sort((x, y) => x - y))
})

it('thinRows keeps the peak of its bucket', () => {
  expect(thinRows(year).some(r => r.b === 900)).toBe(true)
})
