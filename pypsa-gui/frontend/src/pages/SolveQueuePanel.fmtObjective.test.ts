import { describe, expect, it } from 'vitest'
import { fmtObjective } from './SolveQueuePanel'

describe('SolveQueuePanel fmtObjective sign position (UX 2 (6))', () => {
  it.each([
    [-16_310_000, '-€16.31 M'], [-1_500, '-€1.5 k'], [2_500_000, '€2.50 M'], [-0.2, '€0'], [null, '—'],
  ])('%s → %s', (v, out) => expect(fmtObjective(v)).toBe(out))
})
