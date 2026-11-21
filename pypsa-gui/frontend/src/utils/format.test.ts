import { describe, expect, it } from 'vitest'
import { fmtCurrency } from './format'

describe('fmtCurrency sign position (UX 2 (6))', () => {
  it('puts the minus before the €', () => {
    expect(fmtCurrency(-16.31e6)).toBe('-€16.31 M')
    expect(fmtCurrency(-5e6, 1)).toBe('-€5.0 M')
    expect(fmtCurrency(-1500)).toBe('-€1.50 k')
    expect(fmtCurrency(-2.5e9)).toBe('-€2.50 B')
    expect(fmtCurrency(-12)).toBe('-€12.00')
  })
  it('leaves positives, zero and gaps alone', () => {
    expect(fmtCurrency(16.31e6)).toBe('€16.31 M')
    expect(fmtCurrency(0)).toBe('€0.00')
    expect(fmtCurrency(-0.001)).toBe('€0.00')
    expect(fmtCurrency(null)).toBe('—')
  })
})
