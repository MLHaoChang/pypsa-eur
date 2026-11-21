import { describe, expect, it } from 'vitest'
import { fmtResultsObjective } from './TopologyCanvas'

describe('Results pill objective sign position (UX 2 (6))', () => {
  it.each([
    [-96_000, '-€96.0 k'], [-16_310_000, '-€16.31 M'], [2_500_000, '€2.50 M'], [-0.2, '€0'],
  ])('%s → %s', (v, out) => expect(fmtResultsObjective(v)).toBe(out))
})
