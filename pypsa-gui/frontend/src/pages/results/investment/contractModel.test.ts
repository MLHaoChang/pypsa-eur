import { describe, expect, it } from 'vitest'
import {
  CONNECTION, CONTRACT_FIELDS, contractErrors, contractType, isPlaceholder, missingRequired, setField,
  startingPoint, unchangedPrices, unfilledPlaceholders,
} from './contractModel'

const C = [{ type: 'ppa', id: 'ppa1' }, { type: 'cfd', id: 'cfd1' }] as never[]

describe('contract model', () => {
  it('types an untagged contract by its fields, as the server does', () => {
    expect(contractType({ id: 'a', strike: 1 } as never)).toBe('cfd')
    expect(contractType({ id: 'a', availability_eur_per_mw_year: 1 } as never)).toBe('dr')
    expect(contractType({ id: 'a', annual_payment: 1 } as never)).toBe('lease')
    expect(contractType({ id: 'a', fee_eur_per_year: 1 } as never)).toBe('eaas')
    expect(contractType({ id: 'a', tariff_id: 't' } as never)).toBe('retail')
    expect(contractType({ id: 'a', price: 1 } as never)).toBe('ppa')
    expect(contractType({ type: 'swap', id: 'a' } as never)).toBeNull()
  })

  it('removes the fields a change hides', () => {
    const kind = CONTRACT_FIELDS.ppa.find(f => f.key === 'kind')!
    const out = setField({ type: 'ppa', id: 'p', kind: 'baseload', baseload_mw: 2 } as never, kind, 'sleeved')
    expect('baseload_mw' in out).toBe(false)
  })

  it('names blank required text and party fields', () => {
    expect(missingRequired({ type: 'ppa', id: ' ', seller: '', buyer: 'site' } as never))
      .toEqual(['id: required', 'seller: required'])
    expect(missingRequired({ type: 'retail', id: 'r', retailer: 'x', customer: 'y', tariff_id: '' } as never))
      .toEqual(['tariff id: required'])
  })

  it('places a refusal at its contract, the connection agreement or the page', () => {
    const list = contractErrors([
      { loc: ['body', 'commercial', 'contracts', 1, 'cfd', 'strike'], msg: 'bad' },
      { loc: ['body', 'commercial', 'connection', 'envelope'], msg: 'needed' },
      { loc: ['body', 'commercial', 'poc_link'], msg: 'unknown' }], C)
    expect(list.get(1)).toEqual(['cfd.strike: bad'])
    expect(list.get(CONNECTION)).toEqual(['envelope: needed'])
    expect(list.get(-1)).toEqual(['poc_link: unknown'])
    expect(contractErrors({ code: 'x', message: "cfd contract 'cfd1': no asset" }, C).get(1))
      .toEqual(["cfd contract 'cfd1': no asset"])
    expect(contractErrors({ code: 'x', message: 'the connection envelope is stale' }, C).get(CONNECTION))
      .toHaveLength(1)
    expect(contractErrors('solver busy', C).get(-1)).toEqual(['solver busy'])
  })

  it('reads a fill-in marker as the server does: with or without its invisible prefix, any case', () => {
    for (const v of ['\u2060fill-in:seller', 'fill-in:seller', '  FILL-IN:x', '\u2060 Fill-In:asset'])
      expect(isPlaceholder(v)).toBe(true)
    for (const v of ['Solar BV', 'fill in', 'xfill-in:', '', null, 3, ['\u2060fill-in:a']])
      expect(isPlaceholder(v)).toBe(false)
  })

  it('names each field still holding a marker, a list field when any entry is one', () => {
    // Keys sorted, as the server returns them: the names follow the form.
    const c = { asset_ids: ['pv', '\u2060fill-in:asset'], buyer: 'site', id: 'p', kind: 'pay_as_produced',
                price: 55, seller: '\u2060fill-in:seller', tenor_years: 12, type: 'ppa', x: '\u2060fill-in:x' }
    expect(unfilledPlaceholders(c as never)).toEqual(['Seller', 'Assets (generators)', 'X'])
    expect(unfilledPlaceholders({ ...c, seller: 'Solar BV', asset_ids: ['pv'], x: 'y' } as never)).toEqual([])
  })

  it('a starting point takes a free id, keeps the ref and the markers, and copies the payload', () => {
    const ref = { kind: 'contract' as const, id: 'eu-cfd', version: 1, hash: 'c' }
    const payload = { type: 'cfd', id: 'eu-cfd', strike: 85, tenor_years: 18, asset_ids: ['\u2060fill-in:asset'] }
    const taken = [{ type: 'cfd', id: 'cfd_2', strike: 1, tenor_years: 1, asset_ids: ['a'] }]
    const out = startingPoint(payload, ref, taken as never) as unknown as Record<string, unknown>
    expect(out).toEqual({ ...payload, id: 'cfd_3', library_ref: ref })
    ;(out.asset_ids as string[]).push('x')
    expect(payload.asset_ids).toEqual(['\u2060fill-in:asset'])
  })

  it('lists the price fields still equal to the source, never a non-price one', () => {
    const src = { type: 'ppa', price: 55, indexation_pct_per_year: 0, tenor_years: 12 }
    expect(unchangedPrices({ ...src, price: 55 } as never, src).sort()).toEqual(['indexation_pct_per_year', 'price'])
    expect(unchangedPrices({ ...src, price: 60, indexation_pct_per_year: 1 } as never, src)).toEqual([])
  })
})
