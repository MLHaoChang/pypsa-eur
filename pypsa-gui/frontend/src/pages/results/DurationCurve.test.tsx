// ui-modern 02 slice 2: the duration curve as its own chart type.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ReactElement } from 'react'
import { ChartSourceProvider } from '../../components/charts/chartTheme'
import DurationCurve, { durationRows, type DurationSeries } from './DurationCurve'
import * as shared from './shared'

// jsdom has no layout: give the responsive container a size so the plot draws.
vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  const { cloneElement } = await import('react')
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactElement }) =>
      cloneElement(children, { width: 600, height: 220 } as Record<string, unknown>),
  }
})

afterEach(() => { cleanup(); vi.restoreAllMocks() })

const LOAD: DurationSeries = { key: 'load', name: 'Load', colour: 'var(--color-carrier-load)', csv: 'load_MW' }
const RESIDUAL: DurationSeries = { key: 'residual', name: 'Residual load', colour: 'var(--color-series-2)', csv: 'residual_load_MW' }
const mw = (v: number) => `${v.toFixed(1)} MW`

describe('durationRows', () => {
  it('sorts each series high to low on its own, over 0 to 100 % of hours', () => {
    const rows = durationRows({ load: [10, 30, 20], residual: [5, -4, 12] })
    expect(rows).toEqual([
      { pct: 0, load: 30, residual: 12 },
      { pct: 50, load: 20, residual: 5 },
      { pct: 100, load: 10, residual: -4 },
    ])
  })

  it('gives no rows for no hours, and one row at 0 % for one hour', () => {
    expect(durationRows({ load: [] })).toEqual([])
    expect(durationRows({ load: [7] })).toEqual([{ pct: 0, load: 7 }])
  })

  it('refuses series of different lengths: they would not share the hours axis', () => {
    expect(() => durationRows({ load: [1, 2], residual: [1] })).toThrow(/same number of hours/)
  })
})

describe('<DurationCurve>', () => {
  const rows = durationRows({ load: [10, 30, 20, 25], residual: [5, -4, 12, 8] })

  it('is a ChartFrame: title, number, hint, source line and the out-of-date mark', () => {
    const { container } = render(
      <ChartSourceProvider value={{ line: 'Latest solve · solved', stale: true }}>
        <DurationCurve title="Load duration curve" figure="30 MW peak" hint="sorted descending"
          rows={rows} series={[LOAD]} unit="MW" format={mw} filename="load_duration_curve" />
      </ChartSourceProvider>,
    )
    expect(container.querySelector('[data-chart-frame]')).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Load duration curve' })).toBeTruthy()
    expect(screen.getByText('30 MW peak').hasAttribute('data-chart-figure')).toBe(true)
    expect(screen.getByText('sorted descending')).toBeTruthy()
    expect(screen.getByText('Latest solve · solved')).toBeTruthy()
    expect(screen.getByText('Out of date')).toBeTruthy()
  })

  it('draws one curve per series over % of hours, with the unit above the axis', () => {
    const { container } = render(
      <DurationCurve title="Load duration curve" rows={rows} series={[LOAD, RESIDUAL]}
        unit="MW" format={mw} filename="load_duration_curve" />,
    )
    expect(container.querySelectorAll('path.recharts-area-curve').length).toBe(2)
    const ticks = Array.from(container.querySelectorAll('.recharts-xAxis .recharts-cartesian-axis-tick-value'))
      .map(t => t.textContent)
    // jsdom measures no text, so recharts keeps only the end tick; its words are separate tspans.
    expect(ticks[ticks.length - 1]).toBe('100%')
    expect(Array.from(container.querySelectorAll('text')).some(t => t.textContent === 'MW')).toBe(true)
  })

  it('tells two series apart by more than hue: a dash, a name on each line, a key', () => {
    const { container } = render(
      <DurationCurve title="Load duration curve" rows={rows} series={[LOAD, RESIDUAL]}
        unit="MW" format={mw} filename="load_duration_curve" />,
    )
    const [first, second] = Array.from(container.querySelectorAll('path.recharts-area-curve'))
    expect(first.getAttribute('stroke')).toBe('var(--color-carrier-load)')
    expect(first.getAttribute('stroke-dasharray')).toBeNull()
    expect(second.getAttribute('stroke')).toBe('var(--color-series-2)')
    expect(second.getAttribute('stroke-dasharray')).toBe('6 3')
    const named = Array.from(container.querySelectorAll('[data-direct-label]')).map(t => t.getAttribute('data-direct-label'))
    expect(named).toEqual(['Load', 'Residual load'])
    expect(container.querySelector('.recharts-legend-wrapper')).toBeTruthy()
  })

  it('leaves a single series to its title: no name on the line, no key', () => {
    const { container } = render(
      <DurationCurve title="Price duration curve" rows={durationRows({ price: [40, 90, 60] })}
        series={[{ key: 'price', name: 'Price', colour: 'var(--color-series-1)', csv: 'price_EUR_per_MWh' }]}
        unit="€/MWh" format={v => `${v.toFixed(2)} €/MWh`} filename="prices_duration_curve" />,
    )
    expect(container.querySelectorAll('path.recharts-area-curve').length).toBe(1)
    expect(container.querySelector('[data-direct-label]')).toBeNull()
    expect(container.querySelector('.recharts-legend-wrapper')).toBeNull()
  })

  it('marks the mean, rounded like the KPI tile, only when one is given', () => {
    const price = { key: 'price', name: 'Price', colour: 'var(--color-series-1)', csv: 'price_EUR_per_MWh' }
    const props = { title: 'Price duration curve', rows: durationRows({ price: [400, 450, 422.72] }),
      series: [price], unit: '€/MWh', format: (v: number) => `${v.toFixed(2)} €/MWh`, filename: 'prices_duration_curve' }
    const withMean = render(<DurationCurve {...props} mean={424.24} />)
    expect(Array.from(withMean.container.querySelectorAll('.recharts-reference-line text')).map(t => t.textContent))
      .toEqual(['mean 424.2 €/MWh'])
    cleanup()
    const without = render(<DurationCurve {...props} mean={null} />)
    expect(without.container.querySelector('.recharts-reference-line')).toBeNull()
  })

  it('gives each curve on a page its own gradient, so one never paints the other', () => {
    const { container } = render(
      <>
        <DurationCurve title="A" rows={rows} series={[LOAD]} unit="MW" format={mw} filename="a" />
        <DurationCurve title="B" rows={rows} series={[LOAD]} unit="MW" format={mw} filename="b" />
      </>,
    )
    const ids = Array.from(container.querySelectorAll('linearGradient')).map(g => g.id)
    expect(ids.length).toBe(2)
    expect(new Set(ids).size).toBe(2)
    const fills = Array.from(container.querySelectorAll('path.recharts-area-area')).map(p => p.getAttribute('fill'))
    expect(fills).toEqual(ids.map(id => `url(#${id})`))
  })

  it('exports what it draws: % of hours, then one column per series', () => {
    const csv = vi.spyOn(shared, 'downloadCSV').mockImplementation(() => {})
    render(
      <DurationCurve title="Load duration curve" rows={rows} series={[LOAD, RESIDUAL]}
        unit="MW" format={mw} filename="load_duration_curve" />,
    )
    fireEvent.click(screen.getByRole('button', { name: /CSV/ }))
    expect(csv).toHaveBeenCalledTimes(1)
    const [file, header, body] = csv.mock.calls[0]
    expect(file).toBe('load_duration_curve.csv')
    expect(header).toEqual(['pct_of_hours', 'load_MW', 'residual_load_MW'])
    expect(body[0]).toEqual([0, 30, 12])
    expect(body.length).toBe(4)
  })

  it('shows a note above the plot when the chart needs explaining', () => {
    render(
      <DurationCurve title="Load duration curve" rows={rows} series={[LOAD]} unit="MW" format={mw}
        filename="load_duration_curve" note="Total system load sorted high-to-low." />,
    )
    expect(screen.getByText('Total system load sorted high-to-low.')).toBeTruthy()
  })
})
