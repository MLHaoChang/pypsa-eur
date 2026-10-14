// S1b: the pure form logic behind the schema-rendered asset cards.
import { describe, expect, it } from 'vitest'
import type { AssetFieldSpec } from '../api/assetSchema'
import { annuity, buildPatch, derivedStored, displayValue, isRevealed, seedForm } from './assetForm'

function spec(key: string, over: Partial<AssetFieldSpec> = {}): AssetFieldSpec {
  return {
    key, technical_name: key, group: 'size', label: key, help: '', unit_display: '', unit_storage: '',
    scale: 1, kind: 'number', options: [], required: false, key_param: false, derived: false,
    reveal_when: null, part: null, blank: 'keep', ...over,
  }
}

const OVERNIGHT = spec('inv_power_overnight', {
  group: 'investment', unit_display: 'EUR/kW', unit_storage: 'EUR/MW', scale: 1e-3, blank: 'clear', part: 'power' })
const SHARE = spec('efficiency_store', { unit_display: '%', unit_storage: 'fraction', scale: 100 })
const COST = spec('marginal_cost', { blank: 'zero' })
const P_NOM = spec('p_nom')
const EXT = spec('p_nom_extendable', { kind: 'bool' })
const MAX = spec('p_nom_max', { blank: 'clear', reveal_when: { p_nom_extendable: true } })
const CAPEX = spec('capital_cost', { derived: true, unit_display: 'EUR/kW/yr', unit_storage: 'EUR/MW/yr', scale: 1e-3 })
const BASIS = spec('outage_rate_basis', { kind: 'enum', options: ['FOR', 'EFORd'], blank: 'clear' })
const BUILD = spec('build_year', { kind: 'int' })

describe('seedForm: stored values become display strings', () => {
  it('scales money to per-kW and fractions to %', () => {
    const fs = seedForm([OVERNIGHT, SHARE], { inv_power_overnight: 213_900, efficiency_store: 0.96 })
    expect(fs).toEqual({ inv_power_overnight: '213.9', efficiency_store: '96' })
  })

  it('blank for null, NaN and infinite; true/false for booleans', () => {
    const fs = seedForm([OVERNIGHT, MAX, EXT], { inv_power_overnight: null, p_nom_max: Infinity, p_nom_extendable: true })
    expect(fs).toEqual({ inv_power_overnight: '', p_nom_max: '', p_nom_extendable: 'true' })
  })

  it('derived fields are not form fields', () => {
    expect(seedForm([CAPEX], { capital_cost: 95_712 })).toEqual({})
  })

  it('no float noise from the scale', () => {
    expect(seedForm([SHARE], { efficiency_store: 0.07 }).efficiency_store).toBe('7')
  })
})

describe('buildPatch: display strings become stored values', () => {
  const current = { p_nom: 10, marginal_cost: 3, inv_power_overnight: 200_000, efficiency_store: 0.9 }

  it('round-trips through the scale', () => {
    const specs = [OVERNIGHT, SHARE, P_NOM]
    expect(buildPatch(specs, seedForm(specs, current), current)).toEqual(
      { inv_power_overnight: 200_000, efficiency_store: 0.9, p_nom: 10 })
  })

  it('applies each blank policy', () => {
    const fs = { inv_power_overnight: '', marginal_cost: '  ', p_nom: '' }
    expect(buildPatch([OVERNIGHT, COST, P_NOM], fs, current)).toEqual(
      { inv_power_overnight: null, marginal_cost: 0, p_nom: 10 })
  })

  it('never sends a derived field', () => {
    expect(buildPatch([CAPEX, P_NOM], { capital_cost: '5', p_nom: '12' }, current)).toEqual({ p_nom: 12 })
  })

  it('booleans, enums and ints', () => {
    expect(buildPatch([EXT, BASIS, BUILD], { p_nom_extendable: 'false', outage_rate_basis: 'EFORd', build_year: '2030' }, {}))
      .toEqual({ p_nom_extendable: false, outage_rate_basis: 'EFORd', build_year: 2030 })
    expect(buildPatch([BASIS], { outage_rate_basis: 'bogus' }, {})).toEqual({ outage_rate_basis: null })
  })

  it('a field missing from the form is not sent', () => {
    expect(buildPatch([P_NOM, COST], { p_nom: '5' }, current)).toEqual({ p_nom: 5 })
  })

  it('an unparseable number keeps the current value', () => {
    expect(buildPatch([P_NOM], { p_nom: 'abc' }, current)).toEqual({ p_nom: 10 })
  })
})

describe('isRevealed', () => {
  it('follows the form value of the named field', () => {
    expect(isRevealed(MAX, { p_nom_extendable: 'true' })).toBe(true)
    expect(isRevealed(MAX, { p_nom_extendable: 'false' })).toBe(false)
    expect(isRevealed(P_NOM, {})).toBe(true)
  })
})

describe('displayValue', () => {
  it('scales a stored value for read-only rows', () => {
    expect(displayValue(CAPEX, 95_712)).toBe(95.712)
    expect(displayValue(CAPEX, null)).toBeNull()
    expect(displayValue(CAPEX, NaN)).toBeNull()
  })
})

describe('derivedStored: the capital cost PyPSA charges', () => {
  it('annuity matches the backend\'s (periodized_costs._annuity)', () => {
    expect(annuity(0.07, 25)).toBeCloseTo(0.0858105, 6)
    expect(annuity(0, 20)).toBe(0.05)
    expect(annuity(0.07, 0)).toBe(0)
  })

  it('a single-part asset priced by an overnight cost: overnight × annuity, whatever the column holds', () => {
    // A pack-priced template generator (IC S3 seeds) stores capital_cost 0.
    const gen = { overnight_cost: 581_394.9, lifetime: 25, discount_rate: 0.07, capital_cost: 0 }
    expect(derivedStored('capital_cost', gen)).toBeCloseTo(581_394.9 * annuity(0.07, 25), 6)
    // …and a stale column after the overnight cost was edited.
    expect(derivedStored('capital_cost', { ...gen, capital_cost: 1 })).toBeCloseTo(49_890, -1)
  })

  it('the column stands without a rate, without an overnight cost, and for any other derived field', () => {
    expect(derivedStored('capital_cost', { overnight_cost: 500_000, lifetime: 25, capital_cost: 42 })).toBe(42)
    expect(derivedStored('capital_cost', { overnight_cost: null, capital_cost: 95_712, lifetime: 25,
      discount_rate: 0.07 })).toBe(95_712)
    expect(derivedStored('lifetime', { lifetime: 25, overnight_cost: 1, discount_rate: 0.07 })).toBe(25)
  })
})
