import { describe, expect, it } from 'vitest'
import { pinnedVersion, replacesInline, urdbRate } from './libraryModel'

const REF = { kind: 'tariff' as const, id: 'nl', version: 2, hash: 'h2' }
const T = { id: 'nl-tou', name: 'NL', jurisdiction: 'NL', valid_from: '2030-01-01', items: [] }

describe('library model', () => {
  it('finds the rate of an upload, never picking among several', () => {
    expect(urdbRate({ label: 'x', energyratestructure: [] })).toEqual(
      { kind: 'rate', rate: { label: 'x', energyratestructure: [] } })
    expect(urdbRate({ items: [{ name: 'only' }] })).toEqual({ kind: 'rate', rate: { name: 'only' } })
    const many = urdbRate({ items: [{ name: 'A', startdate: 1 }, { label: 'B' }] })
    expect(many).toEqual({ kind: 'choose', rates: [{ index: 0, label: 'A (from 1)' },
                                                    { index: 1, label: 'B' }] })
    expect(urdbRate({ items: [{ name: 'A' }, { name: 'B' }] }, 1))
      .toEqual({ kind: 'rate', rate: { name: 'B' } })
    expect(urdbRate({ ElectricTariff: { urdb_response: { label: 'r' } } }))
      .toEqual({ kind: 'rate', rate: { label: 'r' } })
    expect(urdbRate({ ElectricTariff: { urdb_label: 'abc' } }).kind).toBe('error')
    expect(urdbRate({ items: [] }).kind).toBe('error')
    expect(urdbRate([1, 2]).kind).toBe('error')
  })

  it('names the version the project pins', () => {
    expect(pinnedVersion({ poc_link: 'i', import_tariff_ref: REF } as never, 'tariff', 'nl')).toBe(2)
    expect(pinnedVersion({ poc_link: 'i', import_tariff_ref: REF } as never, 'tariff', 'de')).toBeNull()
    expect(pinnedVersion(null, 'tariff', 'nl')).toBeNull()
  })

  it('asks before replacing a hand-made inline tariff, never for a plain switch', () => {
    expect(replacesInline({ poc_link: 'i' } as never, REF)).toBe(false)             // none to replace
    expect(replacesInline({ poc_link: 'i', import_tariff: T } as never, REF)).toBe(true)
    expect(replacesInline({ poc_link: 'i', import_tariff: T, import_tariff_ref: REF } as never, REF))
      .toBe(false)                                                                  // the same item
    const old = { ...REF, id: 'de', hash: 'hd' }
    expect(replacesInline({ poc_link: 'i', import_tariff: T, import_tariff_ref: old } as never,
                          REF, { ...T })).toBe(false)                               // unedited copy
    expect(replacesInline({ poc_link: 'i', import_tariff: { ...T, name: 'edited' },
                            import_tariff_ref: old } as never, REF, T)).toBe(true)
  })
})
