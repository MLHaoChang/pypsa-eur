import { describe, expect, it } from 'vitest'
import { eur } from './LoopPanel'

describe('LoopPanel eur sign position (UX 2 (6))', () => {
  it.each([
    [-96_000, '-€96,000'], [-16_310_000, '-€16.3m'], [2_500_000, '€2.5m'], [-0.2, '€0'],
  ])('%s → %s', (v, out) => expect(eur(v)).toBe(out))
})
