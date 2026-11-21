import { describe, expect, it } from 'vitest'
import { formatObjective } from './ScenariosPanel'

describe('ScenariosPanel formatObjective sign position (UX 2 (6))', () => {
  it.each([
    [-16_310_000, '-€16.31M'], [-2_500, '-€2.50k'], [4_550_000, '€4.55M'], [-0.2, '€0'], [NaN, '—'],
  ])('%s → %s', (v, out) => expect(formatObjective(v)).toBe(out))
})
