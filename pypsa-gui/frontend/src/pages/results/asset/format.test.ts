import { describe, expect, it } from 'vitest'
import { fmtNum, fmtScalar, withUnit } from './format'

describe('fmtNum', () => {
  it('renders two decimals, integers included', () => {
    expect(fmtNum(120)).toBe('120.00')
    expect(fmtNum(58.4321)).toBe('58.43')
    expect(fmtNum(-3.005)).toBe('-3.01')
  })

  it('groups thousands so a nine-digit euro figure is readable', () => {
    expect(fmtNum(1234567.891)).toBe('1,234,567.89')
  })

  it('renders exact zero as 0.00, not exponential', () => {
    expect(fmtNum(0)).toBe('0.00')
    expect(fmtNum(-0)).toBe('0.00')
  })

  it('escapes to exponential for a real value that would round to zero', () => {
    // Otherwise a barely-running asset is indistinguishable from an idle one.
    expect(fmtNum(0.0008)).toBe('8.00e-4')
    expect(fmtNum(-0.0008)).toBe('-8.00e-4')
    // …but 0.005 rounds cleanly and stays decimal.
    expect(fmtNum(0.006)).toBe('0.01')
  })

  it('blanks null, undefined and non-finite rather than printing NaN', () => {
    expect(fmtNum(null)).toBe('')
    expect(fmtNum(undefined)).toBe('')
    expect(fmtNum(NaN)).toBe('')
    expect(fmtNum(Infinity)).toBe('')
    expect(fmtNum(null, { blank: '—' })).toBe('—')
  })

  it('passes non-numbers through, so mixed rows need no type dispatch', () => {
    expect(fmtNum('2026-01-01T00:00:00')).toBe('2026-01-01T00:00:00')
    expect(fmtNum(true)).toBe('yes')
  })

  it('honours an explicit digit count', () => {
    expect(fmtNum(1.23456, { digits: 4 })).toBe('1.2346')
  })
})

describe('fmtScalar', () => {
  it('flattens a dict-valued metric into key: value pairs', () => {
    expect(fmtScalar({ '2030': 120, '2040': 55.5 }))
      .toBe('2030: 120.00   2040: 55.50')
  })

  it('blanks an empty dict', () => {
    expect(fmtScalar({}, { blank: '—' })).toBe('—')
  })

  it('defers to fmtNum for plain values', () => {
    expect(fmtScalar(58.4321)).toBe('58.43')
    expect(fmtScalar(null, { blank: '—' })).toBe('—')
  })
})

describe('withUnit', () => {
  it('appends a unit only when there is one', () => {
    expect(withUnit('Energy', 'MWh')).toBe('Energy (MWh)')
    expect(withUnit('Committed', '')).toBe('Committed')
  })
})
