// Behaviour guards for the chart theme helpers: label placement, reduced
// motion, and the shared legend's filter (ui-modern issue 02 slice 1).
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { Area, Bar, Line, Tooltip } from 'recharts'
import { applyReducedMotion, bandLabelIndices } from './chartTheme'
import { CHART_LEGEND } from '../../pages/results/shared'

afterEach(cleanup)

const rows = (n: number, f: (i: number) => Record<string, number>) => Array.from({ length: n }, (_, i) => f(i))

describe('bandLabelIndices', () => {
  it('labels each band at its widest row', () => {
    const r = rows(20, i => ({ a: i === 3 ? 10 : 1, b: i === 15 ? 10 : 1 }))
    expect([...bandLabelIndices(r, ['a', 'b'], 10)]).toEqual([['a', 3], ['b', 15]])
  })

  it('gives no labels to a stack of more than six series', () => {
    const keys = 'abcdefg'.split('')
    const r = rows(100, i => Object.fromEntries(keys.map((k, j) => [k, i === j * 16 + 8 ? 10 : 0])))
    expect(bandLabelIndices(r, keys, 10).size).toBe(0)
    expect(bandLabelIndices(r, keys.slice(0, 6), 10).size).toBe(6)
  })

  it('keeps names a share of the rows apart: the second of two close peaks is dropped', () => {
    const r = rows(40, i => ({ a: i === 10 ? 10 : 0, b: i === 12 ? 10 : 0 }))
    expect([...bandLabelIndices(r, ['a', 'b'], 10).keys()]).toEqual(['a'])
  })

  it('skips a band too thin against the peak, and an empty chart', () => {
    const r = rows(10, i => ({ a: i === 2 ? 10 : 0, b: i === 7 ? 1 : 0 }))
    expect([...bandLabelIndices(r, ['a', 'b'], 10).keys()]).toEqual(['a'])
    expect(bandLabelIndices(r, ['a'], 0).size).toBe(0)
  })
})

describe('applyReducedMotion', () => {
  const comps = [Area, Bar, Line, Tooltip] as unknown as Array<{ defaultProps?: Record<string, unknown> }>

  it('does nothing unless reduced motion is asked for', () => {
    const before = comps.map(c => c.defaultProps?.isAnimationActive)
    applyReducedMotion(false)
    expect(comps.map(c => c.defaultProps?.isAnimationActive)).toEqual(before)
  })

  it('switches series and tooltip animation off by default', () => {
    applyReducedMotion(true)
    for (const c of comps) expect(c.defaultProps?.isAnimationActive).toBe(false)
  })
})

describe('CHART_LEGEND', () => {
  it('leaves out entries that asked for legendType="none"', () => {
    const payload = [
      { value: 'Solar', type: 'circle', color: '#b45309', payload: {} },
      { value: 'label pass', type: 'circle', color: '#000', payload: { legendType: 'none' } },
    ]
    const { container } = render(<>{CHART_LEGEND.content({ payload } as never)}</>)
    const text = container.textContent ?? ''
    expect(text).toContain('Solar')
    expect(text).not.toContain('label pass')
  })
})
