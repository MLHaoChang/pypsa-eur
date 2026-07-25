import { describe, expect, it } from 'vitest'
import { safeMax, safeMin, safeMinMax } from './numeric'

describe('safeMax / safeMin / safeMinMax', () => {
  it('returns the extremes of a plain array', () => {
    expect(safeMax([1, 5, 3])).toBe(5)
    expect(safeMin([1, 5, 3])).toBe(1)
    expect(safeMinMax([1, 5, 3])).toEqual({ min: 1, max: 5 })
  })

  it('handles negatives without seeding at zero', () => {
    // A naive `let m = 0` implementation returns 0 here instead of -1.
    expect(safeMax([-9, -1, -4])).toBe(-1)
    expect(safeMin([-9, -1, -4])).toBe(-9)
    expect(safeMinMax([-9, -1, -4])).toEqual({ min: -9, max: -1 })
  })

  it('skips NaN and Infinity rather than propagating them', () => {
    expect(safeMax([1, NaN, 7, Infinity])).toBe(7)
    expect(safeMin([1, NaN, -7, -Infinity])).toBe(-7)
    expect(safeMinMax([NaN, 2, Infinity, 8])).toEqual({ min: 2, max: 8 })
  })

  it('returns the 0 sentinel when nothing is finite', () => {
    expect(safeMax([])).toBe(0)
    expect(safeMin([])).toBe(0)
    expect(safeMax([NaN, Infinity])).toBe(0)
    expect(safeMinMax([NaN, NaN])).toEqual({ min: 0, max: 0 })
  })

  it('survives arrays far larger than the JS argument-stack cap', () => {
    // The reason these helpers exist: Math.max(...vals) throws
    // "RangeError: Maximum call stack size exceeded" somewhere around 65k
    // args in Chrome. A multi-year hourly horizon (26 280) or a multi-decade
    // study exceeds that, so the reduce-based form is not optional.
    const big = new Float64Array(300_000)
    big[123_456] = 42
    big[299_999] = -17
    expect(() => Math.max(...(big as unknown as number[]))).toThrow(RangeError)
    expect(safeMax(big)).toBe(42)
    expect(safeMin(big)).toBe(-17)
  })

  it('accepts any ArrayLike, not just Array', () => {
    expect(safeMax(Float64Array.from([2, 9, 4]))).toBe(9)
    expect(safeMinMax({ length: 3, 0: 5, 1: 1, 2: 9 })).toEqual({ min: 1, max: 9 })
  })
})
