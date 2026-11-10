// ui-modern 02 slice 3: the hour-by-day heatmap as its own chart type.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ReactElement } from 'react'
import { ChartSourceProvider } from '../../components/charts/chartTheme'
import HourDayHeatmap, { dayTicks } from './HourDayHeatmap'
import { heatScale, hourDayGrid } from './hourDay'
import * as shared from './shared'

// jsdom has no layout: give the responsive container a size so the map draws.
vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  const { cloneElement } = await import('react')
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactElement }) =>
      cloneElement(children, { width: 518, height: 220 } as Record<string, unknown>),
  }
})

afterEach(() => { cleanup(); vi.restoreAllMocks() })

const eur = (v: number) => `${v.toFixed(1)} €/MWh`
// Two days; 2030-07-02 has no snapshot at 01:00.
const stamps = [
  ...Array.from({ length: 24 }, (_, h) => `2030-07-01T${String(h).padStart(2, '0')}:00:00`),
  ...Array.from({ length: 24 }, (_, h) => `2030-07-02T${String(h).padStart(2, '0')}:00:00`).filter((_, h) => h !== 1),
]
const grid = hourDayGrid(stamps, stamps.map((_, i) => 40 + i))
const base = {
  title: 'Price by hour and day', grid, scale: heatScale([grid]), colour: 'var(--color-carrier-electricity)',
  format: eur, csv: 'price_EUR_per_MWh', filename: 'price_hour_day',
}

describe('<HourDayHeatmap>', () => {
  it('is a ChartFrame: title, number, hint, source line and the out-of-date mark', () => {
    const { container } = render(
      <ChartSourceProvider value={{ line: 'Latest solve · solved', stale: true }}>
        <HourDayHeatmap {...base} figure="63.0 €/MWh mean" hint="system mean" />
      </ChartSourceProvider>,
    )
    expect(container.querySelector('[data-chart-frame]')).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Price by hour and day' })).toBeTruthy()
    expect(screen.getByText('63.0 €/MWh mean').hasAttribute('data-chart-figure')).toBe(true)
    expect(screen.getByText('Latest solve · solved')).toBeTruthy()
    expect(screen.getByText('Out of date')).toBeTruthy()
  })

  it('draws one cell per hour of each day, stronger for a higher value, in the theme colour', () => {
    const { container } = render(<HourDayHeatmap {...base} />)
    const cells = Array.from(container.querySelectorAll('rect[data-cell]'))
    expect(cells.length).toBe(48)
    const valued = cells.filter(c => c.getAttribute('data-cell') === 'value')
    expect(valued.every(c => c.getAttribute('fill') === 'var(--color-carrier-electricity)')).toBe(true)
    const strength = valued.map(c => Number(c.getAttribute('fill-opacity')))
    expect(strength[0]).toBeCloseTo(0.08)
    expect(strength[strength.length - 1]).toBe(1)
    // 518 wide less 38 of padding over two days: each day column is 240 wide.
    expect(cells[24].getAttribute('x')).toBe('240')
  })

  it('hatches an hour with no snapshot and says so in the key', () => {
    const { container } = render(<HourDayHeatmap {...base} />)
    const none = container.querySelectorAll('rect[data-cell="none"]')
    expect(none.length).toBe(1)
    expect(none[0].getAttribute('fill')).toMatch(/^url\(#hd-/)
    expect(screen.getByText('no snapshot')).toBeTruthy()
    cleanup()
    const full = hourDayGrid(stamps.slice(0, 24), stamps.slice(0, 24).map((_, i) => i + 1))
    render(<HourDayHeatmap {...base} grid={full} scale={heatScale([full])} />)
    expect(screen.queryByText('no snapshot')).toBeNull()
  })

  it('names its axes in words: hour of day, and dates', () => {
    const { container } = render(<HourDayHeatmap {...base} />)
    expect(screen.getByText('Hour of day')).toBeTruthy()
    expect(Array.from(container.querySelectorAll('[data-hour-tick]')).map(t => t.textContent))
      .toEqual(['00', '06', '12', '18', '23'])
    expect(Array.from(container.querySelectorAll('[data-day-tick]')).map(t => t.textContent))
      .toEqual(['2030-07-01', '07-02'])
  })

  it('keys the colour with both ends of the scale as text, and says when the scale is shared', () => {
    const { container } = render(
      <HourDayHeatmap {...base} scale={{ min: 0, max: 200 }} scaleNote="same scale as the other storage carriers" />,
    )
    const key = container.querySelector('[data-heat-key]')!
    expect(key.textContent).toContain('0.0 €/MWh')
    expect(key.textContent).toContain('200.0 €/MWh')
    expect(key.textContent).toContain('stronger colour is higher')
    expect(key.textContent).toContain('same scale as the other storage carriers')
    // On the shared scale the top value (87 of 200) is no longer full strength.
    const strongest = Math.max(...Array.from(container.querySelectorAll('rect[data-cell="value"]'))
      .map(c => Number(c.getAttribute('fill-opacity'))))
    expect(strongest).toBeLessThan(0.5)
  })

  it('has a text alternative: its title and one sentence on what it covers', () => {
    const { container } = render(<HourDayHeatmap {...base} />)
    const svg = container.querySelector('svg[role="img"]')!
    expect(svg.querySelector('title')!.textContent).toBe('Price by hour and day')
    expect(svg.querySelector('desc')!.textContent)
      .toBe('2 days by hour of day, 2030-07-01 to 2030-07-02, from 40.0 €/MWh to 86.0 €/MWh. The CSV export holds every value.')
  })

  it('reads a cell out as text when it is pointed at', () => {
    const { container } = render(<HourDayHeatmap {...base} />)
    const readout = container.querySelector('[data-heat-readout]')!
    expect(readout.textContent).toBe('Hover a cell to read its value')
    const svg = container.querySelector('svg[role="img"]')!
    // jsdom's box is at 0,0: day 1 starts at x 30 + 240, hour 3 at y 18 + 3 rows of (182 / 24).
    fireEvent.mouseMove(svg, { clientX: 30 + 240 + 5, clientY: 18 + 3 * (182 / 24) + 1 })
    expect(readout.textContent).toBe('2030-07-02 03:00 · 66.0 €/MWh')
    fireEvent.mouseMove(svg, { clientX: 30 + 240 + 5, clientY: 18 + 1 * (182 / 24) + 1 })
    expect(readout.textContent).toBe('2030-07-02 01:00 · no snapshot')
    fireEvent.mouseLeave(svg)
    expect(readout.textContent).toBe('Hover a cell to read its value')
  })

  it('says why there is no map, in the frame, instead of a blank plot', () => {
    const { container } = render(
      <HourDayHeatmap {...base} unavailable="Pick one period to see hours by day: periods share calendar dates." />,
    )
    expect(screen.getByTestId('chart-zero').textContent).toBe('Pick one period to see hours by day: periods share calendar dates.')
    expect(container.querySelector('rect[data-cell]')).toBeNull()
    cleanup()
    const empty = hourDayGrid([], [])
    render(<HourDayHeatmap {...base} grid={empty} scale={heatScale([empty])} />)
    expect(screen.getByTestId('chart-zero').textContent).toBe('No hours in this window.')
    cleanup()
    const zero = hourDayGrid(stamps, stamps.map(() => 0))
    render(<HourDayHeatmap {...base} grid={zero} scale={heatScale([zero], true)} />)
    expect(screen.getByTestId('chart-zero').textContent).toBe('Every hour is zero in this window.')
  })

  it('exports date, hour and value, with an empty value where there is no snapshot', () => {
    const csv = vi.spyOn(shared, 'downloadCSV').mockImplementation(() => {})
    render(<HourDayHeatmap {...base} />)
    fireEvent.click(screen.getByRole('button', { name: /CSV/ }))
    const [file, header, body] = csv.mock.calls[0]
    expect(file).toBe('price_hour_day.csv')
    expect(header).toEqual(['date', 'hour', 'price_EUR_per_MWh'])
    expect(body.length).toBe(48)
    expect(body[0]).toEqual(['2030-07-01', 0, 40])
    expect(body[25]).toEqual(['2030-07-02', 1, ''])
  })
})

describe('dayTicks', () => {
  const days = (n: number) => Array.from({ length: n }, (_, i) =>
    new Date(Date.UTC(2030, 0, 1 + i)).toISOString().slice(0, 10))

  it('labels every day of a short window', () => {
    expect(dayTicks(days(7))).toEqual([0, 1, 2, 3, 4, 5, 6])
  })
  it('spaces the labels on a window of a few weeks', () => {
    expect(dayTicks(days(31))).toEqual([0, 4, 8, 12, 16, 20, 24, 28])
  })
  it('labels month starts on a full year', () => {
    const t = dayTicks(days(365))
    expect(t.length).toBe(12)
    expect(t.map(i => days(365)[i].slice(8))).toEqual(Array(12).fill('01'))
  })
})
