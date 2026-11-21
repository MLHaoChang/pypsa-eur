import { describe, expect, it } from 'vitest'
import { buildParameterIndex } from './searchIndex'
import { ASSETS_PER_ENTRY, MODEL_NAME_NOTE, formatValue, parameterRows } from './parameterRows'
import { entry } from './testEntries'

const CAT = { groups: [{ id: 'size_limits', plain_name: 'Size and limits', short_help: '', order: 0, guide: null }] }
const P_NOM = entry({ id: 'generator.p_nom', plain_name: 'Capacity', unit_display: 'MW' })
const GC = entry({ id: 'globalconstraint.constant', plain_name: 'Limit', scope: 'GlobalConstraint' })
const rows = (q: string, assets: Record<string, { name: string; [k: string]: unknown }[]>) =>
  parameterRows(buildParameterIndex([P_NOM, GC]).search(q), CAT, assets)

describe('Parameters rows', () => {
  it('an asset value row reads "PV roof › Capacity" with its value, and opens that asset', () => {
    const [r] = rows('capacity', { Generator: [{ name: 'PV roof', p_nom: 2.4 }] })
    expect(r).toMatchObject({ title: 'PV roof › Capacity', subtitle: 'Generator · Size and limits', hint: '2.4 MW' })
    expect(r.target).toEqual({ entry: P_NOM, asset: 'PV roof' })
  })

  it('an entry with no asset in the project says so and goes nowhere', () => {
    const [r] = rows('capacity', {})
    expect(r).toMatchObject({ title: 'Capacity', subtitle: 'Generator · Size and limits · None in this project' })
    expect(r.target).toBeUndefined()
  })

  it('lists several assets, capped, and counts the rest on the last row', () => {
    const gens = Array.from({ length: ASSETS_PER_ENTRY + 3 }, (_, i) => ({ name: `G${i}`, p_nom: i }))
    const rs = rows('capacity', { Generator: gens })
    expect(rs).toHaveLength(ASSETS_PER_ENTRY)
    expect(rs.at(-1)!.subtitle).toMatch(/and 3 more$/)
  })

  it('a model-file-name hit says so in words and never shows the raw name', () => {
    const [r] = rows('p_nom', { Generator: [{ name: 'PV roof', p_nom: 1 }] })
    expect(r.subtitle).toContain(MODEL_NAME_NOTE)
    expect(`${r.title} ${r.subtitle} ${r.hint}`).not.toMatch(/p_nom/)
  })

  it('an entry whose class has no Properties card is left out', () => {
    expect(rows('limit', {})).toEqual([])
  })
})

describe('formatValue', () => {
  const opt = entry({ id: 'line.type', plain_name: 'Type', options: ['a', 'b'], options_plain: ['Overhead', 'Cable'] })
  it.each([
    [P_NOM, 2.5, '2.5 MW'], [P_NOM, null, 'Not set'], [P_NOM, Infinity, 'No limit'],
    [entry({ id: 'x.k', plain_name: 'k', scale: 1000, unit_display: 'kW' }), 0.0025, '2.5 kW'],
    [P_NOM, true, 'Yes'], [opt, 'b', 'Cable'], [opt, 'zz', 'zz'],
  ])('%#', (e, raw, shown) => { expect(formatValue(e, raw)).toBe(shown) })
})
