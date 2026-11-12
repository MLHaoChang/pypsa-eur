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

const HOUR = 3_600_000
const stamp = (i: number, step = HOUR) => new Date(Date.UTC(2025, 0, 1) + i * step).toISOString().slice(0, 19)
const year = Array.from({ length: 8760 }, (_, i) => ({
  snapshot: stamp(i), a: i % 24, b: i === 4321 ? 900 : 1,
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
  const idx = out.map(r => Date.parse(r.snapshot))
  expect(idx).toEqual([...idx].sort((x, y) => x - y))
})

it('thinRows keeps the peak of its bucket', () => {
  expect(thinRows(year).some(r => r.b === 900)).toBe(true)
})

import { render, screen } from '@testing-library/react'
import { ThinNote } from './shared'

it('ThinNote says how many rows are drawn, only when it thinned', () => {
  const { rerender } = render(<ThinNote rows={year} />)
  expect(screen.getByTestId('thin-note').textContent).toMatch(/Drawn: 1,4\d\d of 8,760 hours/)
  rerender(<ThinNote rows={year.slice(0, 100)} />)
  expect(screen.queryByTestId('thin-note')).toBeNull()
})

it('ThinNote counts time steps, not hours, when the step is not an hour', () => {
  const threeHourly = Array.from({ length: 2920 }, (_, i) => ({ snapshot: stamp(i, 3 * HOUR), a: 1 }))
  render(<ThinNote rows={threeHourly} />)
  const text = screen.getByTestId('thin-note').textContent ?? ''
  expect(text).toMatch(/of 2,920 time steps/)
  expect(text).toMatch(/the largest time step of each stretch/)
  expect(text).not.toMatch(/hour/)
})

it('ThinNote reads the step from shortStamp keys (`MM-DD HH:MM`) too', () => {
  const rows = year.map(r => ({ t: r.snapshot.slice(5, 16).replace('T', ' '), a: r.a }))
  render(<ThinNote rows={rows} />)
  expect(screen.getByTestId('thin-note').textContent).toMatch(/of 8,760 hours/)
})

import { readFileSync } from 'node:fs'

// The full-year tabs of ui-modern 06b: every time-axis chart in these files
// must opt into timeAxis and draw thinRows, or Recharts measures 8,760 labels.
it.each(['LoadFlow', 'LostLoadTab', 'BranchLoading', 'ReserveCommitments', 'EnergyFlowsTab', 'LoadShapeTab', 'Prices'])(
  '%s time axes use timeAxis',
  (name) => {
    const src = readFileSync(`src/pages/results/${name}.tsx`, 'utf8')
    const axes = src.match(/<XAxis dataKey="(snapshot|t)"[^>]*>/g) ?? []
    expect(axes.length).toBeGreaterThan(0)
    for (const a of axes) expect(a).toContain('timeAxis(')
    if (name !== 'BranchLoading') expect(src).toContain('<ThinNote')
  },
)
