import { describe, expect, it } from 'vitest'
import { displayName } from './displayName'

describe('displayName — raw ids in plain words (UX S6, finding G1)', () => {
  it.each([
    ['grid_import', 'Grid import'],
    ['it_bus', 'IT bus'],
    ['site_transformer', 'Site transformer'],
    ['ups-battery', 'UPS battery'],
    ['pv_rooftop', 'PV rooftop'],
    ['h2_storage', 'H2 storage'],
    ['ccgt', 'CCGT'],
    ['grid_import_2', 'Grid import 2'],
  ])('%s → %s', (id, name) => {
    expect(displayName(id)).toBe(name)
  })

  it.each(['Bus 0', 'Data Center Energy Hub', 'Battery1', 'IEEE bus 14'])(
    'leaves a name a person already wrote (%s) alone', id => {
      expect(displayName(id)).toBe(id)
    })

  it('returns empty for empty', () => {
    expect(displayName('')).toBe('')
  })
})
