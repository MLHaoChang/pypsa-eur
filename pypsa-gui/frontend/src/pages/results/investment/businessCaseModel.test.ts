import { describe, expect, it } from 'vitest'
import type { BusinessCaseOptions, LineItem } from '../../../api/businessCase'
import {
  edited, parsePaste, placeRefusals, placeValidation, provenanceOf, sourceKind,
  unitNotAllowed, unitsFor, withKind, yearsOf,
} from './businessCaseModel'

export const OPTS: BusinessCaseOptions = {
  template_id: 'eh_h2_hub',
  variants: {},
  offered_rows: [],
  categories: {
    revenue: ['product_sale', 'by_product', 'other'],
    opex: ['staff', 'maintenance'],
    capex: ['facility', 'equipment'],
  },
  escalations: ['opex', 'capex', 'contract'],
  typed_units: {
    revenue: ['MWh', 'kg', 't', 'unit'],
    opex: ['MWh', 'kg', 't', 'unit'],
    capex: ['MWh', 'kg', 't', 'MW', 'unit', 'lump'],
  },
  catalogue_version: '1',
  outputs: [{ id: 'served_h2.h2_load', label: 'H2 served to h2_load', units: ['MWh_H2', 'kg'], derived: false }],
  outputs_reason: null,
  horizon: [2030, 2049],
  base_year: 2030,
  refusals: [],
  refusals_reason: null,
}

const line = (kw: Partial<LineItem> = {}): LineItem => ({
  name: 'H2 sales', kind: 'revenue', category: 'product_sale', unit: 'kg',
  quantity_source: 'typed', quantity: 10, escalation: 'opex', ...kw,
})

describe('unitsFor: the select offers only what the source allows', () => {
  it('a typed line takes its kind’s typed units', () => {
    expect(unitsFor(line(), OPTS)).toEqual(['MWh', 'kg', 't', 'unit'])
    expect(unitsFor(line({ kind: 'capex', category: 'facility', unit: 'lump' }), OPTS)).toContain('lump')
  })
  it('a linked line takes its output’s units', () => {
    const l = line({ quantity_source: 'linked:served_h2.h2_load@1', unit: 'kg' })
    expect(sourceKind(l.quantity_source)).toBe('linked')
    expect(unitsFor(l, OPTS)).toEqual(['MWh_H2', 'kg'])
  })
  it('a share takes share only', () => {
    expect(unitsFor(line({ quantity_source: 'share_of:revenue', unit: 'share' }), OPTS)).toEqual(['share'])
  })
  it('keeps a stored unit the source refuses, so the refusal is visible', () => {
    const l = line({ unit: 'lump' })
    expect(unitsFor(l, OPTS)).toEqual(['MWh', 'kg', 't', 'unit', 'lump'])
    expect(unitNotAllowed(l, OPTS)).toBe(true)
    expect(unitNotAllowed(line(), OPTS)).toBe(false)
  })
})

describe('refusal placement (G9)', () => {
  it('puts each refusal on its line and column, in words', () => {
    const placed = placeRefusals([
      { code: 'business_case_refused', refusal: 'price_not_established', field: 'lines[1].price', message: 'x' },
      { code: 'business_case_refused', refusal: 'double_count', field: 'lines[0].quantity_source', message: 'x' },
    ])
    expect(placed).toEqual([
      { line: 1, column: 'price', kind: 'price_not_established', message: expect.stringMatching(/^Price: /) },
      { line: 0, column: 'quantity_source', kind: 'double_count', message: expect.stringMatching(/^Quantity from: /) },
    ])
  })
  it('places a PUT validation error from its loc, with the column label', () => {
    const placed = placeValidation([
      { loc: ['body', 'business_case', 'inputs', 'lines', '2', 'life_years'], msg: 'Input should be greater than or equal to 1' },
    ])
    expect(placed[0]).toMatchObject({ line: 2, column: 'life_years', message: 'Life (years): Input should be greater than or equal to 1' })
  })
})

describe('parsePaste (BCA-SCOPE-1 Q2)', () => {
  const years = [2030, 2031, 2032]
  it('reads a spreadsheet row from the focused year', () => {
    expect(parsePaste('1\t2,000\t3\n', years, 2030)).toEqual({ values: { 2030: 1, 2031: 2000, 2032: 3 }, bad: [], extra: 0 })
  })
  it('reads a column the same way, from a later year', () => {
    expect(parsePaste('5\n6', years, 2031)).toEqual({ values: { 2031: 5, 2032: 6 }, bad: [], extra: 0 })
  })
  it('never reads text as 0 and counts cells past the last year', () => {
    expect(parsePaste('n/a\t\t7\t8', years, 2030)).toEqual({ values: { 2032: 7 }, bad: [2030], extra: 1 })
  })
})

describe('line edits', () => {
  it('an edit to an assumed column makes it stated', () => {
    const l = line({ kind: 'opex', category: 'staff', price: 5,
      assumed: { price: { key: 'k', value: 5, unit: 'EUR/unit', source: 'Pack', source_year: 2025, pack: 'p', status: 'assumed' } } })
    expect(provenanceOf(l)).toBe('assumed')
    expect(provenanceOf(edited(l, { price: 6 }))).toBe('stated')
    expect(provenanceOf(edited(l, { note: 'x' }))).toBe('assumed')
  })
  it('a kind change picks that kind’s category and an allowed unit', () => {
    const l = withKind(line({ unit: 'lump', kind: 'capex', category: 'facility', life_years: 20 }), 'opex', OPTS)
    expect(l).toMatchObject({ kind: 'opex', category: 'staff', unit: 'MWh' })
    expect(l.life_years).toBeUndefined()
  })
  it('years run start..end, or the horizon', () => {
    expect(yearsOf(line({ start_year: 2030, end_year: 2032 }), null)).toEqual([2030, 2031, 2032])
    expect(yearsOf(line(), [2030, 2031])).toEqual([2030, 2031])
    expect(yearsOf(line(), null)).toEqual([])
  })
})
