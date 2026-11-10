// ui-modern 02 slice 5: the tornado chart type (VISUALS-1; UX conditions T1–T4).
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import TornadoChart, {
  deltaMoney, niceTicks, rowEnds, signedMoney, tickText, tornadoDomain, wrapLabel, type TornadoBar,
} from './TornadoChart'

afterEach(cleanup)

const bar = (key: string, npv_low: number | null, npv_high: number | null, more: Partial<TornadoBar> = {}): TornadoBar => ({
  key, label: `Driver ${key}`, unit: 'per unit', low_value: 0.049, high_value: 0.091, npv_low, npv_high,
  swing: npv_low != null && npv_high != null ? Math.abs(npv_high - npv_low) : null, ...more,
})
const pct = (v: number) => `${(v * 100).toFixed(1)} %`
const show = (rows: TornadoBar[], base: number | null, domain?: [number, number]) => render(
  <TornadoChart title="Battery NPV at each driver’s low and high value" measure="Battery NPV" rows={rows} base={base}
    formatInput={pct} filename="t" domain={domain} />)
const row = (key: string) => screen.getAllByTestId('tornado-bar').find(el => el.getAttribute('data-key') === key)!
const values = (key: string) => [...within(row(key)).getByTestId('tornado-values').querySelectorAll('text')].map(t => t.textContent)
const sides = (key: string) => [...row(key).querySelectorAll('rect')].map(r => r.getAttribute('data-side'))
const xOf = (el: Element) => Number(el.getAttribute('x'))

describe('the tornado’s numbers (T1)', () => {
  it('writes money in the one notation, with the sign spelled out', () => {
    expect(signedMoney(4338981, 'EUR')).toBe('€ 4.3 m')
    expect(signedMoney(-350000, 'EUR')).toBe('−€ 350 k')
    expect(deltaMoney(4338981, 3531384, 'EUR')).toBe('+€ 808 k')
    expect(deltaMoney(2934407, 3531384, 'EUR')).toBe('−€ 597 k')
  })

  it('keeps neighbouring axis ticks apart and carries no currency on them', () => {
    const { ticks, step } = niceTicks(3_500_000, 3_560_000, 5)
    const labels = ticks.map(t => tickText(t, step, 3_560_000))
    expect(new Set(labels).size).toBe(labels.length)
    expect(labels.join(' ')).not.toMatch(/€|EUR/)
    expect(tickText(-200_000, 100_000, 400_000)).toBe('−200 k')
    expect(tickText(0, 100_000, 400_000)).toBe('0 k')
  })

  it('heads the frame with the largest swing and its driver, and names the unit once on the axis', () => {
    show([bar('a', 3.4e6, 3.6e6), bar('b', 2.9e6, 4.3e6)], 3.5e6)
    const frame = document.querySelector('[data-chart-frame]')!
    expect(frame.querySelector('[data-chart-figure]')!.textContent).toBe('€ 1.4 m largest swing')
    expect(frame.querySelector('header')!.textContent).toContain('· Driver b')
    const axisTexts = [...screen.getByTestId('tornado-plot').querySelectorAll('g[aria-hidden] text')].map(t => t.textContent)
    expect(axisTexts.filter(t => t!.includes('€'))).toEqual(['Battery NPV, €'])
  })
})

describe('each bar end (T2)', () => {
  it('is labelled with the input, the result and the change against the base case', () => {
    show([bar('a', 4338981, 2934407)], 3531384)
    // The lower result sits on the left, whichever input gave it.
    expect(values('a')).toEqual([
      'High 9.1 %: € 2.9 m (−€ 597 k)',
      'Low 4.9 %: € 4.3 m (+€ 808 k)',
    ])
    expect(sides('a')).toEqual(['below', 'above'])
    expect(row('a').textContent).toContain('swing € 1.4 m')
  })

  it('splits the bar at the base case: danger below, success above', () => {
    show([bar('a', 2e6, 5e6)], 3e6)
    const [below, above] = [...row('a').querySelectorAll('rect')]
    expect(below.getAttribute('fill')).toBe('var(--color-danger)')
    expect(above.getAttribute('fill')).toBe('var(--color-success)')
    const baseX = Number(screen.getByTestId('tornado-base').querySelector('line')!.getAttribute('x1'))
    expect(xOf(below) + Number(below.getAttribute('width'))).toBeCloseTo(baseX, 3)
    expect(xOf(above)).toBeCloseTo(baseX, 3)
  })

  it('draws a row that stays on one side of the base case in one colour', () => {
    show([bar('a', 4e6, 5e6), bar('wide', 1e6, 6e6)], 3e6)
    expect(sides('a')).toEqual(['above'])
  })
})

describe('the base case and zero (T3)', () => {
  it('labels zero only when it is in range', () => {
    show([bar('a', 2e6, 5e6)], 3e6)
    expect(screen.queryByTestId('tornado-zero')).toBeNull()
    expect(screen.getByTestId('tornado-base').textContent).toBe('Base case € 3 m')
  })

  it('puts the zero and base-case labels on separate lines, so they cannot collide near zero', () => {
    show([bar('a', -40_000, 60_000)], 5_000)
    const zero = screen.getByTestId('tornado-zero').querySelector('text')!
    const base = screen.getByTestId('tornado-base').querySelector('text')!
    expect(zero.textContent).toBe('Zero Battery NPV')
    expect(base.textContent).toBe('Base case € 5 k')
    expect(Math.abs(Number(zero.getAttribute('y')) - Number(base.getAttribute('y')))).toBeGreaterThanOrEqual(11)
    expect(values('a')[0]).toBe('Low 4.9 %: −€ 40 k (−€ 45 k)')
  })
})

describe('a case that is not established (T4)', () => {
  it('is said in words, draws one side only and never a bar at 0', () => {
    show([bar('full', 2e6, 5e6), bar('half', 2.5e6, null)], 3e6)
    expect(values('half')).toEqual(['Low 4.9 %: € 2.5 m (−€ 500 k)', 'High case: not established'])
    expect(sides('half')).toEqual(['below'])
    expect(row('half').textContent).toContain('swing not established')
    expect(screen.getByTestId('tornado-missing-note').textContent).toMatch(/listed last/)
    expect(screen.queryByTestId('tornado-zero')).toBeNull()
  })

  it('leaves the free side to the missing case', () => {
    const r = bar('x', null, 4e6)
    expect(rowEnds(r, 3e6).map(e => e.which)).toEqual(['Low', 'High'])
    expect(rowEnds(bar('y', null, 2e6), 3e6).map(e => e.which)).toEqual(['High', 'Low'])
  })

  it('draws no bar for a row with neither case, and no plot when no row has one', () => {
    show([bar('full', 2e6, 5e6), bar('none', null, null)], 3e6)
    expect(sides('none')).toEqual([])
    expect(values('none')).toEqual(['Low case: not established', 'High case: not established'])
    cleanup()
    show([bar('none', null, null)], null)
    expect(screen.queryByTestId('tornado-plot')).toBeNull()
    expect(screen.getByTestId('tornado-values').textContent).toContain('not established')
    expect(screen.getByTestId('chart-zero').textContent).toBe('No case has a result yet.')
  })

  it('keeps the domain to what is known: a null is not a 0', () => {
    expect(tornadoDomain([bar('a', 2e6, null)], 3e6)![0]).toBeGreaterThan(1.9e6)
    expect(tornadoDomain([bar('a', null, null)], null)).toBeNull()
  })
})

describe('a shared scale (VISUALS-1)', () => {
  it('takes a fixed domain, so two options side by side share one axis', () => {
    show([bar('a', 2e6, 5e6)], 3e6, [0, 10e6])
    const first = xOf(row('a').querySelector('rect')!)
    cleanup()
    show([bar('a', 2e6, 5e6)], 3e6)
    expect(xOf(row('a').querySelector('rect')!)).toBeLessThan(first)
    expect(screen.queryByTestId('tornado-zero')).toBeNull()
  })
})

describe('long driver labels', () => {
  it('wrap at spaces', () => {
    expect(wrapLabel('Energy price level (multiplier on the tariff)', 20))
      .toEqual(['Energy price level', '(multiplier on the', 'tariff)'])
  })
})
