import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import type { Tariff } from '../../../api/types'
import {
  errorsByField, errorsUnder, isWindowed, normaliseTariff, parseIntList, parseNumberList,
} from './tariffModel'

// The P2 oracle, read from the backend's fixtures (vitest runs from the frontend root).
export const H3: Tariff = JSON.parse(readFileSync(resolve(process.cwd(),
  '../backend/tests/fixtures/investment_case/bills/h3_us_ci.json'), 'utf-8')).tariff

describe('tariff model', () => {
  it('writes H3 back exactly as the P2 model holds it', () => {
    expect(normaliseTariff(structuredClone(H3))).toEqual(H3)
    expect(isWindowed(H3.items[0])).toBe(true)
    expect(isWindowed(H3.items[1])).toBe(false)
  })

  it('leaves defaults and empty optionals out', () => {
    const t = normaliseTariff({ id: 't', name: 't', jurisdiction: 'DE', valid_from: '2030-01-01',
      valid_to: null, unsupported_fields: [],
      items: [{ id: 'e', kind: 'energy', unit: 'per_kwh', settlement: '15min', measured_on: 'import',
                direction: 'cost', tiers: [], ratchet: null,
                periods: [{ name: 'all', rate: 0.1, months: [], start_hour: null, tier_rates: null }] }] })
    expect(t).toEqual({ id: 't', name: 't', jurisdiction: 'DE', valid_from: '2030-01-01',
      items: [{ id: 'e', kind: 'energy', unit: 'per_kwh', periods: [{ name: 'all', rate: 0.1 }] }] })
  })

  it('parses comma lists and refuses junk', () => {
    expect(parseIntList('6, 7 8')).toEqual([6, 7, 8])
    expect(parseIntList('')).toBeUndefined()
    expect(parseIntList('6, x')).toBeNull()
    expect(parseIntList('1.5')).toBeNull()
    expect(parseNumberList('0.2, 0.23')).toEqual([0.2, 0.23])
  })

  it('maps server errors to fields, with or without the request prefix', () => {
    const byField = errorsByField({ errors: [
      { loc: ['items', 0, 'periods', 1, 'rate'], msg: 'must be a number' },
      { loc: ['body', 'payload', 'items', 2, 'ratchet'], msg: 'exactly one of' },
      { loc: ['valid_from'], msg: 'bad date' }] })
    expect(errorsUnder(byField, 'items.0')).toEqual(['items.0.periods.1.rate: must be a number'])
    expect(errorsUnder(byField, 'items.2')).toEqual(['items.2.ratchet: exactly one of'])
    expect(byField.valid_from).toEqual(['bad date'])
    expect(errorsByField([{ loc: ['body', 'tariff', 'id'], msg: 'x' }])).toEqual({ id: ['x'] })
  })
})
