import { describe, expect, it } from 'vitest'
import { pinnedVersions, replacesInline, urdbDate, urdbRate } from './libraryModel'

const REF = { kind: 'tariff' as const, id: 'nl', version: 2, hash: 'h2' }
const T = { id: 'nl-tou', name: 'NL', jurisdiction: 'NL', valid_from: '2030-01-01', items: [] }

describe('library model', () => {
  it('finds the rate of an upload, never picking among several', () => {
    expect(urdbRate({ label: 'x', energyratestructure: [] })).toEqual(
      { kind: 'rate', rate: { label: 'x', energyratestructure: [] } })
    expect(urdbRate({ items: [{ name: 'only' }] })).toEqual({ kind: 'rate', rate: { name: 'only' } })
    const many = urdbRate({ items: [{ name: 'A', startdate: 1704067200 }, { label: 'B' }] })
    const rates = [{ index: 0, label: 'A (from 2024-01-01)' }, { index: 1, label: 'B' }]
    expect(many).toEqual({ kind: 'choose', rates })
    // A pick keeps the choice visible and changeable (review #2).
    expect(urdbRate({ items: [{ name: 'A', startdate: 1704067200 }, { label: 'B' }] }, 1))
      .toEqual({ kind: 'rate', rate: { label: 'B' }, rates, index: 1 })
    expect(urdbRate({ items: [{ name: 'A' }] }, 3).kind).toBe('error')
    expect(urdbRate({ ElectricTariff: { urdb_response: { label: 'r' } } }))
      .toEqual({ kind: 'rate', rate: { label: 'r' } })
    const reopt = urdbRate({ ElectricTariff: { urdb_label: 'abc' } })
    expect(reopt.kind === 'error' && reopt.message).toMatch(/no urdb_response/)
    expect(urdbRate({ items: [] }).kind).toBe('error')
    expect(urdbRate([1, 2]).kind).toBe('error')
  })

  it('writes URDB epoch dates as dates', () => {
    expect(urdbDate(1704067200)).toBe('2024-01-01')
    expect(urdbDate('1704067200')).toBe('2024-01-01')
    expect(urdbDate('soon')).toBe('soon')
    expect(urdbDate(undefined)).toBe('')
  })

  it('names the versions the project pins: tariff ref, contracts and agreement', () => {
    const c = {
      poc_link: 'i', import_tariff_ref: REF,
      contracts: [{ id: 'a', library_ref: { kind: 'contract', id: 'ppa', version: 1, hash: 'x' } },
                  { id: 'b', library_ref: { kind: 'contract', id: 'ppa', version: 3, hash: 'y' } },
                  { id: 'c' }],
      connection: { library_ref: { kind: 'connection_agreement', id: 'ca', version: 4, hash: 'z' } },
    } as never
    expect(pinnedVersions(c, 'tariff', 'nl')).toEqual([2])
    expect(pinnedVersions(c, 'tariff', 'de')).toEqual([])
    expect(pinnedVersions(c, 'contract', 'ppa')).toEqual([3, 1])
    expect(pinnedVersions(c, 'connection_agreement', 'ca')).toEqual([4])
    expect(pinnedVersions(c, 'contract', 'ca')).toEqual([])
    expect(pinnedVersions(null, 'tariff', 'nl')).toEqual([])
  })

  it('asks before replacing a hand-made inline tariff, never for a Library switch', () => {
    expect(replacesInline({ poc_link: 'i' } as never)).toBe(false)                 // none to replace
    expect(replacesInline(null)).toBe(false)
    expect(replacesInline({ poc_link: 'i', import_tariff: T } as never)).toBe(true)
    expect(replacesInline({ poc_link: 'i', import_tariff_id: 'x' } as never)).toBe(true)
    // With a ref the inline copy IS the Library item's (the server refuses any other).
    expect(replacesInline({ poc_link: 'i', import_tariff: T, import_tariff_ref: REF } as never))
      .toBe(false)
  })
})
