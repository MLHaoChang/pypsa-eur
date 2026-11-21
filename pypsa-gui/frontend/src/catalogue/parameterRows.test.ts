import { describe, expect, it } from 'vitest'
import { buildParameterIndex } from './searchIndex'
import {
  ASSETS_PER_ENTRY, GUIDE_NOTE, MODEL_NAME_NOTE, NONE_NOTE, formatValue, isTonneBus, moreNote, parameterRows, splitAssetWords,
} from './parameterRows'
import { entry } from './testEntries'

const CAT_GROUPS = [{ id: 'size_limits', plain_name: 'Size and limits', short_help: '', order: 0, guide: null }]
const P_NOM = entry({ id: 'generator.p_nom', plain_name: 'Capacity', unit_display: 'MW' })
const COST = entry({ id: 'generator.capital_cost', plain_name: 'Overnight cost', unit_display: 'EUR/kW', scale: 0.001,
  guide: 'What one unit of size costs to build, before any financing.' })
const GC = entry({ id: 'globalconstraint.constant', plain_name: 'Limit', scope: 'GlobalConstraint' })
const ENTRIES = [P_NOM, COST, GC]
type A = { name: string; [k: string]: unknown }
const rows = (q: string, assets: Record<string, A[]>) =>
  parameterRows(q, buildParameterIndex(ENTRIES), { groups: CAT_GROUPS, entries: ENTRIES }, assets)

describe('Parameters rows', () => {
  it('an asset value row reads "PV roof: Capacity" with its value, and opens that asset', () => {
    const [r] = rows('capacity', { Generator: [{ name: 'PV roof', p_nom: 2.4, bus: 'B' }] })
    expect(r).toMatchObject({ title: 'PV roof: Capacity', subtitle: 'Generator · Size and limits', hint: '2.4 MW' })
    expect(r.target).toEqual({ entry: P_NOM, asset: 'PV roof' })
  })

  it('a value on a tonne bus is in t/h, and its costs per tonne at scale 1', () => {
    const assets = { Bus: [{ name: 'NH3', carrier: 'NH3', unit: 't' }], Generator: [{ name: 'Feed', p_nom: 3, capital_cost: 500, bus: 'NH3' }] }
    expect(rows('capacity', assets)[0].hint).toBe('3 t/h')
    expect(rows('overnight cost', assets)[0].hint).toBe('500 EUR/(t/h)')
    expect(rows('overnight cost', { Generator: [{ name: 'PV', capital_cost: 500_000, bus: 'AC' }] })[0].hint).toBe('500 EUR/kW')
  })

  it('a missing value says so in words', () => {
    expect(rows('capacity', { Generator: [{ name: 'PV roof' }] })[0].hint).toBe('— Not set')
  })

  it('an entry with no asset in the project says so, and has nowhere to go', () => {
    const [r] = rows('capacity', {})
    expect(r).toMatchObject({ title: 'Capacity', subtitle: `Generator · Size and limits · ${NONE_NOTE}` })
    expect(r.target).toBeUndefined()
  })

  it('lists several assets, capped, and says how to narrow on the last row', () => {
    const gens = Array.from({ length: ASSETS_PER_ENTRY + 3 }, (_, i) => ({ name: `Diesel ${i}`, p_nom: i }))
    const rs = rows('capacity', { Generator: [...gens, { name: 'PV roof', p_nom: 1 }] })
    expect(rs).toHaveLength(ASSETS_PER_ENTRY)
    expect(rs.at(-1)!.subtitle).toBe(`Generator · Size and limits · ${moreNote(4)}`)
  })

  it('words that name an asset narrow the rows to it', () => {
    const gens = [{ name: 'Diesel 1', p_nom: 1 }, { name: 'Diesel 2', p_nom: 2 }, { name: 'PV roof', p_nom: 3 }]
    expect(rows('pv capacity', { Generator: gens }).map(r => r.title)).toEqual(['PV roof: Capacity'])
    expect(rows('capacity dies', { Generator: gens }).map(r => r.title)).toEqual(['Diesel 1: Capacity', 'Diesel 2: Capacity'])
  })

  it('a model-file-name hit says so in words and never shows the raw name', () => {
    const [r] = rows('p_nom', { Generator: [{ name: 'PV roof', p_nom: 1 }] })
    expect(r.subtitle).toContain(MODEL_NAME_NOTE)
    expect(`${r.title} ${r.subtitle} ${r.hint}`).not.toMatch(/p_nom/)
  })

  it('a guide-text hit shows a short plain snippet', () => {
    const [r] = rows('financing', { Generator: [{ name: 'PV', capital_cost: 1 }] })
    expect(r.subtitle).toMatch(/^Generator · Size and limits · …?.*before any financing\.$/)
  })

  it('a long guide snippet is cut at word boundaries, never inside a word', () => {
    const guide = 'Every unit of installed capacity carries an annualised overnight cost that the optimiser weighs against '
      + 'the energy it delivers across the whole year, including financing and maintenance over its expected lifetime.'
    const long = entry({ id: 'generator.capital_cost', plain_name: 'Overnight cost', guide })
    const [r] = parameterRows('weighs', buildParameterIndex([long]), { groups: CAT_GROUPS, entries: [long] },
      { Generator: [{ name: 'PV', capital_cost: 1 }] })
    const snip = r.subtitle!.split(' · ').at(-1)!
    expect(snip).toMatch(/^….*…$/)
    const inner = snip.slice(1, -1)
    expect(guide).toContain(inner)
    const start = guide.indexOf(inner)
    expect(guide[start - 1]).toBe(' ')
    expect(guide[start + inner.length]).toBe(' ')
  })

  it('an entry whose class has no Properties card is one row that opens in the guide', () => {
    expect(rows('limit', {})).toEqual([{
      id: 'param-globalconstraint.constant', entryId: 'globalconstraint.constant', title: 'Limit',
      subtitle: `Global constraint · Size and limits · ${GUIDE_NOTE}`, guideOnly: true,
    }])
  })

  it('every row names its entry, for Shift+Enter', () => {
    expect(rows('capacity', { Generator: [{ name: 'PV roof', p_nom: 1 }] }).map(r => r.entryId)).toEqual(['generator.p_nom'])
  })
})

describe('asset words', () => {
  it('a word that also starts a parameter name stays with the parameter', () => {
    expect(splitAssetWords('capacity cap', ['Capital site'], w => 'capacity'.startsWith(w)))
      .toEqual({ parameterQuery: 'capacity cap', assetWords: [] })
    expect(splitAssetWords('roof capacity', ['PV roof'], w => 'capacity'.startsWith(w)))
      .toEqual({ parameterQuery: 'capacity', assetWords: ['roof'] })
  })

  it('a tonne bus: its own unit, else its carrier\'s catalogue unit', () => {
    expect(isTonneBus({ name: 'a', unit: 't' })).toBe(true)
    expect(isTonneBus({ name: 'b', unit: 'MWh', carrier: 'ore' })).toBe(false)
    expect(isTonneBus({ name: 'c', carrier: 'ore' })).toBe(true)
    expect(isTonneBus({ name: 'd', carrier: 'AC' })).toBe(false)
    expect(isTonneBus(undefined)).toBe(false)
  })
})

describe('formatValue', () => {
  const opt = entry({ id: 'line.type', plain_name: 'Type', options: ['a', 'b'], options_plain: ['Overhead', 'Cable'] })
  it.each([
    [P_NOM, 2.5, '2.5 MW'], [P_NOM, null, '— Not set'], [P_NOM, Infinity, 'No limit'],
    [entry({ id: 'x.k', plain_name: 'k', scale: 1000, unit_display: 'kW' }), 0.0025, '2.5 kW'],
    [P_NOM, true, 'Yes'], [opt, 'b', 'Cable'], [opt, 'zz', 'zz'],
  ])('%#', (e, raw, shown) => { expect(formatValue(e, raw)).toBe(shown) })
})
