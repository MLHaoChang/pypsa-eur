// Gap 03: the recipe card's arithmetic is the inverse of the server's reader (services/recipes.py).
import { describe, expect, it } from 'vitest'
import type { FlowRecipe } from '../api/flow'
import { electricityLabel, inputLabel, recipeFigure, recipePatch } from './recipe'

// eh_ammonia's haber_bosch, as the server reads it.
const EFF = 4.043671653861707
const EFF2 = -4.6416904582655665
const HB: FlowRecipe = {
  label: 'Haber-Bosch', product: { carrier: 'NH3', label: 'NH3' }, per: 't', product_per: 5.166,
  electricity: { value: 5.166 / EFF * 1000, unit: 'kWh' },
  inputs: [{ port: 2, carrier: 'H2', label: 'Hydrogen', value: -EFF2 * 5.166 / EFF, unit: 'MWh' }],
}
const EL: FlowRecipe = {
  label: 'Electrolyser', product: { carrier: 'H2', label: 'Hydrogen' }, per: 'MWh', product_per: 1,
  electricity: { value: 1 / 0.68, unit: 'MWh' }, inputs: [],
}

describe('recipePatch', () => {
  it('turns the read rates back into the same efficiencies', () => {
    const p = recipePatch(HB, String(HB.electricity.value), [String(HB.inputs[0].value)])
    expect(typeof p).toBe('object')
    const patch = p as Record<string, number>
    expect(patch.efficiency).toBeCloseTo(EFF, 12)
    expect(patch.efficiency2).toBeCloseTo(EFF2, 12)
  })

  it('reads electricity per MWh for an energy product, with no inputs', () => {
    expect(recipePatch(EL, '2', [])).toEqual({ efficiency: 0.5 })
  })

  it('a changed rate moves only its own efficiency', () => {
    const patch = recipePatch(HB, String(HB.electricity.value), ['6']) as Record<string, number>
    expect(patch.efficiency).toBeCloseTo(EFF, 12)
    expect(patch.efficiency2).toBeCloseTo(-6 / (HB.electricity.value / 1000), 12)
  })

  it.each([['0'], ['-1'], ['abc'], [''], ['Infinity']])('refuses %j, opening with the field label', raw => {
    const el = recipePatch(HB, raw, ['5'])
    const input = recipePatch(HB, '1000', [raw])
    expect(el).toMatch(/^Electricity per tonne of NH3: (must be above 0|enter a number)$/)
    expect(input).toMatch(/^Hydrogen per tonne of NH3: (must be above 0|enter a number)$/)
  })

  it('labels name the product the rates are per', () => {
    expect(electricityLabel(EL)).toBe('Electricity per MWh of Hydrogen')
    expect(inputLabel(HB, 0)).toBe('Hydrogen per tonne of NH3')
  })
})

it('the row figure spells out electricity and names no id', () => {
  expect(recipeFigure(HB)).toBe('5.93 MWh Hydrogen + 1,277.55 kWh electricity per t NH3')
  expect(recipeFigure(EL)).toBe('1.47 MWh electricity per MWh Hydrogen')
})
