// Phase 2 plan Task 0.3: the palette's data lives in one module that the
// sidebar renders and the 3D asset library's matching test reads, so a
// palette change is seen by both. These tests pin that the module agrees
// with what the sidebar shows and what the creation form submits.
import { describe, it, expect } from 'vitest'
import { PALETTE_SECTIONS_DATA, PALETTE_ITEM_IDS, PALETTE_COMPONENT_TYPE, paletteDefaults } from './paletteData'
import { PALETTE_ICONS } from './paletteIcons'
import { FIELD_MAP } from './CreationForm'

describe('palette data', () => {
  it('lists every item once, in section order', () => {
    const ids = PALETTE_SECTIONS_DATA.flatMap(s => s.items.map(i => i.id))
    expect(ids).toEqual(PALETTE_ITEM_IDS)
    expect(new Set(ids).size).toBe(ids.length)
    expect(ids.length).toBe(18)
  })

  it('has an icon in the sidebar for every item, and no icon for anything else', () => {
    expect(Object.keys(PALETTE_ICONS).sort()).toEqual([...PALETTE_ITEM_IDS].sort())
  })

  it('names a PyPSA class for every item', () => {
    for (const id of PALETTE_ITEM_IDS) expect(PALETTE_COMPONENT_TYPE[id], id).toMatch(/^(Bus|Line|Transformer|Generator|StorageUnit|Store|Link|Load)$/)
  })

  it.each(PALETTE_ITEM_IDS)('%s: default carrier and port carriers agree with the creation form', id => {
    const fields = FIELD_MAP[id] ?? []
    const d = paletteDefaults(id)
    const carrierField = fields.find(f => f.key === 'carrier')
    expect(d.carrier, `${id} carrier`).toBe(carrierField?.defaultValue)
    for (const port of ['bus', 'bus0', 'bus1', 'bus2'] as const) {
      const f = fields.find(x => x.key === port) as { busCarrierFilter?: string } | undefined
      expect(d.ports?.[port], `${id} ${port}`).toBe(f?.busCarrierFilter)
    }
  })
})
