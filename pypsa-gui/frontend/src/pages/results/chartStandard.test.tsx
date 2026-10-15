// UX overhaul S4: the chart standard (docs/superpowers/plans/2026-10-14-ux-overhaul.md
// §3 "Chart standard", findings R6 and A1). Guards the shared pieces every
// results chart is built from.
import { readFileSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { isValidElement, type ReactElement } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { ChartCard, ChartZero, extremeDot, extremeOf, isAllZero, refLine, shortStamp, yAxisLabel } from './shared'

afterEach(cleanup)

const here = resolve(__dirname)

function luminance(hex: string): number {
  const c = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map(x => (x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4))
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
}
const contrast = (a: string, b: string) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

describe('the y-axis unit (R6)', () => {
  it('is drawn horizontally above the axis, never rotated over the tick labels', () => {
    const label = yAxisLabel('MW') as { content: (p: { viewBox: { x: number; y: number } }) => ReactElement }
    const el = label.content({ viewBox: { x: 10, y: 30 } })
    expect(isValidElement(el)).toBe(true)
    const { container } = render(<svg>{el}</svg>)
    const text = container.querySelector('text')!
    expect(text.textContent).toBe('MW')
    expect(text.getAttribute('transform')).toBeNull()
    expect(Number(text.getAttribute('y'))).toBeLessThan(30)
    expect(text.getAttribute('text-anchor')).toBe('start')
  })

  it('has room: every results chart leaves at least 18 px above the plot for it', () => {
    const offenders: string[] = []
    for (const f of readdirSync(here).filter(f => f.endsWith('.tsx') && !f.endsWith('.test.tsx'))) {
      const src = readFileSync(resolve(here, f), 'utf8')
      for (const m of src.matchAll(/margin=\{\{\s*top:\s*(\d+)/g)) {
        if (Number(m[1]) < 18) offenders.push(`${f}: top ${m[1]}`)
      }
    }
    expect(offenders).toEqual([])
  })
})

describe('ChartCard titles (R6)', () => {
  it('wrap instead of being cut off with an ellipsis', () => {
    render(<ChartCard title="System-cost waterfall" hint="annualised, horizon (all periods)"><div /></ChartCard>)
    const title = screen.getByText('System-cost waterfall')
    expect(title.className).not.toMatch(/\btruncate\b/)
    expect(screen.getByText('annualised, horizon (all periods)').className).not.toMatch(/\btruncate\b/)
  })
})

describe('axis text contrast (A1)', () => {
  const css = readFileSync(resolve(here, '../../index.css'), 'utf8')
  const light = css.slice(css.indexOf('@theme'), css.indexOf(':root[data-theme="dark"]'))
  const dark = css.slice(css.indexOf(':root[data-theme="dark"]'))
  const token = (block: string, name: string) =>
    new RegExp(`--color-${name}:\\s*(#[0-9a-fA-F]{6})`).exec(block)![1]

  it.each([['light', light], ['dark', dark]] as const)('reaches 4.5 : 1 on every chart surface in %s', (_, block) => {
    const axis = token(block, 'axis')
    for (const surface of ['bg', 'bg-2', 'panel']) {
      expect(contrast(axis, token(block, surface))).toBeGreaterThanOrEqual(4.5)
    }
  })
})

describe('reference lines (S4b)', () => {
  it('draw a limit, a target or a mean dashed, in a semantic colour, never the accent', () => {
    for (const tone of ['limit', 'target', 'mean'] as const) {
      const p = refLine(tone, 'mean', 136.64, '€/MWh')
      expect(p.strokeDasharray).toBeTruthy()
      expect(p.stroke).not.toMatch(/accent/)
      expect(p.stroke).toMatch(/^var\(--color-/)
    }
    expect(refLine('limit', 'import cap', 40, 'MW').stroke).toBe('var(--color-danger)')
  })

  it('can stand on the x axis, for a goal measured along it', () => {
    const p = refLine('target', 'goal', 20, 'MWh', 'x')
    expect(p).toMatchObject({ x: 20, stroke: 'var(--color-success)' })
    expect(p).not.toHaveProperty('y')
    expect(p.label.value).toBe('goal 20.0 MWh')
  })

  it('label the line with its figure rounded like the KPI tile', () => {
    const p = refLine('mean', 'mean', 136.64, '€/MWh')
    expect(p).toMatchObject({ y: 136.64 })
    expect(p.label.value).toBe('mean 136.6 €/MWh')
  })
})

describe('the extreme point (S4b)', () => {
  it('finds the highest finite value and where it sits', () => {
    const rows = [{ s: 'a', v: 1 }, { s: 'b', v: Number.NaN }, { s: 'c', v: 7 }, { s: 'd', v: 7 }, { s: 'e', v: 2 }]
    expect(extremeOf(rows, 'v')).toEqual({ index: 2, value: 7 })
  })

  it('is marked with its figure, rounded like the KPI tile', () => {
    const d = extremeDot('2030-07-01T14:00:00', 424.24, 'worst hour', 'MW')
    expect(d).toMatchObject({ x: '2030-07-01T14:00:00', y: 424.24 })
    expect(d.label.value).toBe('worst hour 424 MW')
    expect(d.fill).not.toMatch(/accent/)
  })

  it('is absent when nothing is above zero', () => {
    expect(extremeOf([{ v: 0 }, { v: 0 }], 'v')).toBeNull()
    expect(extremeOf([], 'v')).toBeNull()
  })
})

describe('an all-zero series (S4b)', () => {
  it('is detected across every plotted key, ignoring gaps', () => {
    expect(isAllZero([{ a: 0, b: 0 }, { a: null, b: 0 }], ['a', 'b'])).toBe(true)
    expect(isAllZero([{ a: 0, b: 0.01 }], ['a', 'b'])).toBe(false)
    expect(isAllZero([], ['a'])).toBe(true)
  })

  it('reads as a sentence instead of empty axes', () => {
    render(<ChartZero>No load was shed in this window.</ChartZero>)
    const el = screen.getByTestId('chart-zero')
    expect(el.textContent).toBe('No load was shed in this window.')
    expect(el.querySelector('svg')).toBeNull()
  })
})

describe('time ticks (S4b)', () => {
  it('are ISO and 24 h', () => {
    expect(shortStamp('2030-07-01T14:00:00')).toBe('07-01 14:00')
    expect(shortStamp('2030-07-01 23:00:00')).toBe('07-01 23:00')
  })
})
