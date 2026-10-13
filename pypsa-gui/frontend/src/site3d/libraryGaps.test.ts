// Visual-layers plan 3 S4: "the library, not the model, is the limit" — the
// generic objects grouped into one status line.
import { describe, it, expect } from 'vitest'
import { libraryGaps, libraryGapLine } from './libraryGaps'
import { buildSiteLayout, type SiteInput } from './layout'
import type { Generator, Link, Load, StorageUnit, Store } from '../api/types'

const o = (type: 'Generator' | 'StorageUnit' | 'Load', name: string, carrier: string, generic?: true) => ({ type, name, carrier, generic })

describe('libraryGaps', () => {
  it('groups the generic objects by class and carrier, first seen first, and skips the specific ones', () => {
    const gaps = libraryGaps([
      o('Generator', 'g1', 'gas'),
      o('Generator', 'w1', 'water', true),
      o('StorageUnit', 's1', 'water', true),
      o('Generator', 'w2', 'water', true),
    ])
    expect(gaps).toEqual([
      { cls: 'Generator', carrier: 'water', names: ['w1', 'w2'] },
      { cls: 'StorageUnit', carrier: 'water', names: ['s1'] },
    ])
  })
  it('nothing generic → no line', () => {
    expect(libraryGaps([o('Generator', 'g1', 'gas')])).toEqual([])
    expect(libraryGapLine([])).toBeNull()
  })
  it('one line, one clause per gap, singular and plural, an empty carrier named as such', () => {
    expect(libraryGapLine([
      { cls: 'Generator', carrier: 'water', names: ['w1', 'w2'] },
      { cls: 'Store', carrier: '', names: ['st'] },
    ])).toBe("'water' Generator drawn as generic blocks (2) · no carrier Store drawn as a generic block")
  })
})

describe('the layout marks generic objects (utils/assetTypes genericUnless)', () => {
  const input: SiteInput = {
    buses: [{ name: 'B', v_nom: 110, offset: [0, 0] }, { name: 'H', v_nom: 0, offset: [300, 0] }],
    generators: [
      { name: 'genset', bus: 'B', carrier: 'diesel', p_nom: 5 } as Generator,
      { name: 'tidal', bus: 'B', carrier: 'tidal', p_nom: 5 } as Generator,
    ],
    storageUnits: [
      { name: 'bess', bus: 'B', carrier: 'battery', p_nom: 5, max_hours: 2 } as StorageUnit,
      { name: 'odd', bus: 'B', carrier: 'gravity', p_nom: 5, max_hours: 2 } as StorageUnit,
    ],
    stores: [{ name: 'gas tank', bus: 'B', carrier: 'gas', e_nom: 10 } as Store],
    loads: [
      { name: 'hall', bus: 'B', carrier: 'AC', p_set: 10 } as Load,
      { name: 'cooling', bus: 'B', carrier: 'water', p_set: 10 } as Load,
    ],
    transformers: [], lines: [],
    links: [
      { name: 'tie', bus0: 'B', bus1: 'H', carrier: 'DC', p_nom: 10 } as Link,
      { name: 'pipe', bus0: 'B', bus1: 'H', carrier: 'water pipeline', p_nom: 10 } as Link,
    ],
    busCarrier: name => (name === 'H' ? 'water' : 'AC'),
  }
  it('flags the carriers the library has no type for, and only those', () => {
    const { objects } = buildSiteLayout(input)
    const generic = objects.filter(x => x.generic).map(x => `${x.type}:${x.name}`).sort()
    expect(generic).toEqual(['Bus:H', 'Link:pipe', 'Load:cooling', 'StorageUnit:odd', 'Store:gas tank', 'Generator:tidal'].sort())
    expect(libraryGapLine(libraryGaps(objects))).toContain("'tidal' Generator drawn as a generic block")
    expect(libraryGapLine(libraryGaps(objects))).toContain("'water pipeline' Link drawn as a generic block")
  })
})
