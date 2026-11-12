import { describe, expect, it } from 'vitest'
import { findSnapshot, sanitizeRows } from './reportCharts'

describe('sanitizeRows', () => {
  it('turns NaN / Infinity into null, cuts long text, keeps finite numbers', () => {
    const rows = sanitizeRows(['a', 'b', 'c'], [[Number.NaN, 'y'.repeat(250), 3.5], [-Infinity, undefined, 0]])
    expect(rows[0][0]).toBeNull()
    expect((rows[0][1] as string).length).toBe(200)
    expect(rows[0][2]).toBe(3.5)
    expect(rows[1]).toEqual([null, null, 0])
  })
  it('caps the table at 10000 cells', () => {
    const rows = sanitizeRows(['a', 'b'], Array.from({ length: 6000 }, () => [1, 2]))
    expect(rows).toHaveLength(5000)
  })
})

describe('findSnapshot', () => {
  it('matches on view, figure and title together', () => {
    const c = { view: 'v', figure: 'f', title: 't' } as never
    expect(findSnapshot([c], 'v', 'f', 't')).toBe(c)
    expect(findSnapshot([c], 'v', 'f', 'other')).toBeUndefined()
  })
})
