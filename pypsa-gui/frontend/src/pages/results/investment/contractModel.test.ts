import { describe, expect, it } from 'vitest'
import { CONNECTION, CONTRACT_FIELDS, contractErrors, contractType, missingRequired, setField } from './contractModel'

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
})
