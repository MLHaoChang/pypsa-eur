// The shared chart theme (ui-modern issue 02, slice 1; VISUALS-1). Guards the
// API later chart slices and the KPI strip (issue 03) build on.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import {
  CHART_HEIGHT, CHART_TICK, ChartFrame, ChartSourceLine, ChartSourceProvider, SERIES_TOKENS, chartFigure,
  carrierColour, directLabel, seriesDash, thickestIndex,
} from './chartTheme'

afterEach(cleanup)

const css = readFileSync(resolve(__dirname, '../../index.css'), 'utf8')
const tokenOf = (v: string) => /^var\((--[\w-]+)\)$/.exec(v)?.[1]

describe('carrierColour', () => {
  it('maps a carrier to its token, exact or by substring', () => {
    expect(carrierColour('solar')).toBe('var(--color-carrier-solar)')
    expect(carrierColour('offwind-ac')).toBe('var(--color-carrier-offwind)')
    expect(carrierColour('onwind')).toBe('var(--color-carrier-wind)')
    expect(carrierColour('OCGT')).toBe('var(--color-carrier-gas)')
    expect(carrierColour('H2')).toBe('var(--color-carrier-h2)')
    expect(carrierColour('battery')).toBe('var(--color-carrier-storage)')
  })

  it('gives an unknown carrier a series token picked by its position, wrapping', () => {
    expect(carrierColour('my-custom', 0)).toBe('var(--color-series-1)')
    expect(carrierColour('my-custom', 2)).toBe('var(--color-series-3)')
    expect(carrierColour('', SERIES_TOKENS)).toBe('var(--color-series-1)')
    expect(carrierColour(null, -1)).toBe('var(--color-series-2)')
  })

  it('names only tokens index.css defines', () => {
    const names = ['solar', 'wind', 'offwind', 'gas', 'ccgt', 'coal', 'lignite', 'oil', 'nuclear', 'biomass',
      'geothermal', 'hydro', 'ror', 'rooftop', 'battery', 'load', 'electricity', 'heat', 'h2', 'electrolysis', 'grid',
      'link_inflows', 'link_outflows']
    const tokens = [...names.map(n => carrierColour(n)),
      ...Array.from({ length: SERIES_TOKENS }, (_, i) => carrierColour('', i))]
    for (const t of tokens) {
      const name = tokenOf(t)
      expect(name, t).toBeTruthy()
      expect(css, `${name} is defined`).toMatch(new RegExp(`${name}:\\s*#[0-9a-f]{6};`))
    }
  })
})

describe('sizes, ticks and series styles', () => {
  it('has three heights, small to large', () => {
    expect(Object.keys(CHART_HEIGHT)).toEqual(['sm', 'md', 'lg'])
    expect(CHART_HEIGHT.sm).toBeLessThan(CHART_HEIGHT.md)
    expect(CHART_HEIGHT.md).toBeLessThan(CHART_HEIGHT.lg)
  })

  it('sets ticks in mono with tabular figures, at the DENSITY-1 size', () => {
    expect(CHART_TICK.fontFamily).toBe('var(--font-family-mono)')
    expect(CHART_TICK.style.fontVariantNumeric).toBe('tabular-nums')
    expect(CHART_TICK.fontSize).toBe(10)
  })

  it('cycles line styles so neighbouring series differ by more than hue', () => {
    expect(seriesDash(0)).toBeUndefined()
    expect(new Set([0, 1, 2, 3].map(seriesDash)).size).toBe(4)
    expect(seriesDash(4)).toBe(seriesDash(0))
  })

  it('finds where a band is widest, and -1 for an all-zero series', () => {
    expect(thickestIndex([{ a: 1 }, { a: -5 }, { a: 3 }], 'a')).toBe(1)
    expect(thickestIndex([{ a: 0 }, { a: null }], 'a')).toBe(-1)
  })

  it('draws a direct label only at its own row', () => {
    const Label = directLabel('Solar', 2)
    expect(Label({ x: 10, y: 20, index: 1 })).toBeNull()
    expect(directLabel('Solar', -1)({ x: 10, y: 20, index: -1 })).toBeNull()
    const { container } = render(<svg>{Label({ x: 10, y: 20, index: 2 })}</svg>)
    const text = container.querySelector('text')!
    expect(text.textContent).toBe('Solar')
    expect(Number(text.getAttribute('y'))).toBeGreaterThan(20)
    const line = render(<svg>{directLabel('Load', 0, 'line')({ x: 10, y: 20, index: 0 })}</svg>)
    expect(Number(line.container.querySelector('text')!.getAttribute('y'))).toBeLessThan(20)
  })
})

describe('ChartFrame', () => {
  it('heads the chart with its title and its number', () => {
    render(<ChartFrame title="Dispatch stack · Electricity" figure="47.2 MW peak load"><div /></ChartFrame>)
    expect(screen.getByRole('heading', { name: 'Dispatch stack · Electricity' })).toBeTruthy()
    expect(screen.getByText('47.2 MW peak load').hasAttribute('data-chart-figure')).toBe(true)
  })

  it('names the source run and window, plus what only this chart applies', () => {
    render(
      <ChartSourceProvider value={{ line: 'Latest solve · solved · 2030-07-01 00:00 → 2030-07-07 23:00', stale: false }}>
        <ChartFrame title="Load duration curve" basis="each hour weighted × 52.14"><div /></ChartFrame>
      </ChartSourceProvider>,
    )
    expect(screen.getByText('Latest solve · solved · 2030-07-01 00:00 → 2030-07-07 23:00 · each hour weighted × 52.14')).toBeTruthy()
    expect(screen.queryByText('Out of date')).toBeNull()
  })

  it('marks the chart out of date and dims it when the solve no longer matches the network', () => {
    const { container } = render(
      <ChartSourceProvider value={{ line: 'Latest solve · solved', stale: true }}>
        <ChartFrame title="Lost load over time"><div data-testid="plot" /></ChartFrame>
      </ChartSourceProvider>,
    )
    expect(screen.getByText('Out of date')).toBeTruthy()
    expect(screen.getByTestId('plot').parentElement!.className).toContain('opacity-60')
    expect(container.querySelector('[data-chart-source]')).toBeTruthy()
  })

  it('shows no source footer outside a solved Results view', () => {
    const { container } = render(<ChartFrame title="x"><div /></ChartFrame>)
    expect(container.querySelector('[data-chart-source]')).toBeNull()
    expect(render(<ChartSourceLine />).container.innerHTML).toBe('')
  })
})

describe('chartFigure', () => {
  it('rounds like the KPI tile and says what the number is', () => {
    expect(chartFigure('424.20 MW', 'worst hour')).toBe('424 MW worst hour')
    expect(chartFigure('47.24 MW')).toBe('47.2 MW')
  })
})

describe('thickestIndex on a long series', () => {
  it('keeps the label off the plot edges', () => {
    const rows = Array.from({ length: 100 }, (_, i) => ({ a: i === 0 || i === 99 ? 50 : i === 40 ? 9 : 1 }))
    expect(thickestIndex(rows, 'a')).toBe(40)
  })
})
