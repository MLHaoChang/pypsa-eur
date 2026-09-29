import { describe, it, expect } from 'vitest'
import { buildSiteLayout, type SiteInput } from './layout'
import type { Generator, Load, StorageUnit, Store, Transformer, Line, Link } from '../api/types'

// Minimal component factories: only the fields the layout reads, cast to the
// full PyPSA shapes so a field rename in api/types.ts fails here at compile
// time rather than silently building an empty site.
const gen = (o: Partial<Generator>): Generator => ({ name: 'g', bus: 'B', carrier: 'gas', p_nom: 10, ...o } as Generator)
const su  = (o: Partial<StorageUnit>): StorageUnit => ({ name: 's', bus: 'B', carrier: 'battery', p_nom: 10, max_hours: 4, ...o } as StorageUnit)
const st  = (o: Partial<Store>): Store => ({ name: 'st', bus: 'B', carrier: 'H2', e_nom: 100, ...o } as Store)
const ld  = (o: Partial<Load>): Load => ({ name: 'l', bus: 'B', carrier: 'AC', p_set: 20, ...o } as Load)
const tr  = (o: Partial<Transformer>): Transformer => ({ name: 't', bus0: 'B', bus1: 'X', s_nom: 100, v_nom_0: 110, v_nom_1: 33, ...o } as Transformer)
const ln  = (o: Partial<Line>): Line => ({ name: 'ln', bus0: 'B', bus1: 'Y', s_nom: 200, ...o } as Line)
const lk  = (o: Partial<Link>): Link => ({ name: 'lk', bus0: 'B', bus1: 'Z', carrier: 'DC', p_nom: 50, ...o } as Link)

const empty: SiteInput = {
  bus: { name: 'B', v_nom: 110 },
  generators: [], storageUnits: [], stores: [], loads: [], transformers: [], lines: [], links: [],
}

describe('buildSiteLayout', () => {
  it('an empty bus is still a switchyard, and only that', () => {
    const { objects, halfSizeM } = buildSiteLayout(empty)
    expect(objects.map(o => o.kind)).toEqual(['switchyard'])
    expect(objects[0]).toMatchObject({ type: 'Bus', name: 'B' })
    expect(halfSizeM).toBeGreaterThanOrEqual(250)
  })

  it('ignores components attached to other buses', () => {
    const { objects } = buildSiteLayout({
      ...empty,
      generators: [gen({ name: 'mine' }), gen({ name: 'theirs', bus: 'OTHER' })],
      transformers: [tr({ name: 'touching', bus1: 'B', bus0: 'X' }), tr({ name: 'elsewhere', bus0: 'P', bus1: 'Q' })],
    })
    expect(objects.map(o => o.name).sort()).toEqual(['B', 'mine', 'touching'])
  })

  it('a BESS is N containers from its MWh, and the count survives the draw cap', () => {
    const small = buildSiteLayout({ ...empty, storageUnits: [su({ p_nom: 10, max_hours: 4 })] }).objects[1]
    expect(small.kind).toBe('bess')
    expect(small.type).toBe('StorageUnit')
    // 40 MWh at 4 MWh/container = 10 containers + one PCS skid per row of 8 = 2 rows.
    expect(small.parts.length).toBe(10 + 2)
    expect(small.summary).toContain('40 MWh')
    expect(small.summary).toContain('10 containers')

    const huge = buildSiteLayout({ ...empty, stores: [st({ carrier: 'battery', e_nom: 4000 })] }).objects[1]
    expect(huge.kind).toBe('bess')
    expect(huge.type).toBe('Store')
    expect(huge.summary).toContain('1000 containers')
    expect(huge.summary).toMatch(/each box = \d+/)
    expect(huge.parts.length).toBeLessThan(200)
  })

  it('classifies by carrier: PV, wind, thermal, H₂ store, electrolyser link', () => {
    const { objects } = buildSiteLayout({
      ...empty,
      generators: [gen({ name: 'pv', carrier: 'solar' }), gen({ name: 'w', carrier: 'onwind' }), gen({ name: 'ccgt', carrier: 'CCGT' })],
      stores: [st({ name: 'h2', carrier: 'H2' })],
      links: [lk({ name: 'ely', carrier: 'electrolysis', bus1: 'H2BUS' }), lk({ name: 'hvdc', carrier: 'DC' })],
    })
    const kinds = Object.fromEntries(objects.map(o => [o.name, o.kind]))
    expect(kinds).toMatchObject({ pv: 'pv', w: 'wind', ccgt: 'thermal', h2: 'h2store', ely: 'electrolyser', hvdc: 'feeder' })
  })

  it('an electrolyser is only one when this bus is its electrical side', () => {
    const { objects } = buildSiteLayout({
      ...empty,
      links: [lk({ name: 'ely-in', carrier: 'electrolysis', bus0: 'B', bus1: 'H' }), lk({ name: 'ely-out', carrier: 'electrolysis', bus0: 'H', bus1: 'B' })],
    })
    const kinds = Object.fromEntries(objects.map(o => [o.name, o.kind]))
    expect(kinds['ely-in']).toBe('electrolyser')
    expect(kinds['ely-out']).toBe('feeder')
  })

  it('PV land scales with p_nom and is reported as the area number', () => {
    const one = buildSiteLayout({ ...empty, generators: [gen({ carrier: 'solar', p_nom: 1 })] }).objects[1]
    const ten = buildSiteLayout({ ...empty, generators: [gen({ carrier: 'solar', p_nom: 10 })] }).objects[1]
    expect(one.areaM2).toBeCloseTo(25_000, -2)
    expect(ten.areaM2).toBeCloseTo(250_000, -2)
    expect(ten.footprint[0] * ten.footprint[1]).toBeGreaterThan(one.footprint[0] * one.footprint[1])
  })

  it('wind is one tower, one nacelle and three blades per 5 MW', () => {
    const w = buildSiteLayout({ ...empty, generators: [gen({ carrier: 'wind', p_nom: 12 })] }).objects[1]
    expect(w.summary).toContain('3 × 5 MW')
    expect(w.parts.length).toBe(3 * 5)
    expect(w.parts.filter(p => p.rotN != null).length).toBe(9)
  })

  it('a transformer reads its voltages and a feeder names the far bus', () => {
    const { objects } = buildSiteLayout({ ...empty, transformers: [tr({})], lines: [ln({ bus0: 'Y', bus1: 'B' })] })
    const t = objects.find(o => o.kind === 'transformer')!
    const f = objects.find(o => o.kind === 'feeder')!
    expect(t.summary).toContain('110/33 kV')
    expect(f.summary).toContain('to Y')
    expect(f.type).toBe('Line')
  })

  it('every object carries the exact {type, name} the properties panel switches on', () => {
    const { objects } = buildSiteLayout({
      ...empty,
      generators: [gen({ name: 'G1' })], storageUnits: [su({ name: 'S1' })], stores: [st({ name: 'ST1' })],
      loads: [ld({ name: 'L1' })], transformers: [tr({ name: 'T1' })], lines: [ln({ name: 'LN1' })], links: [lk({ name: 'LK1' })],
    })
    expect(objects.map(o => [o.type, o.name])).toEqual(expect.arrayContaining([
      ['Bus', 'B'], ['Generator', 'G1'], ['StorageUnit', 'S1'], ['Store', 'ST1'],
      ['Load', 'L1'], ['Transformer', 'T1'], ['Line', 'LN1'], ['Link', 'LK1'],
    ]))
  })

  it('objects do not overlap the switchyard or each other, and the half-size contains them all', () => {
    const { objects, halfSizeM } = buildSiteLayout({
      ...empty,
      generators: [gen({ name: 'a', p_nom: 30 }), gen({ name: 'b', carrier: 'solar', p_nom: 5 }), gen({ name: 'c', carrier: 'wind', p_nom: 10 })],
      storageUnits: [su({ name: 's1', p_nom: 20 }), su({ name: 's2', p_nom: 5 })],
      loads: [ld({ name: 'dc', p_set: 50 })],
      transformers: [tr({ name: 't1' }), tr({ name: 't2' })],
      lines: [ln({ name: 'l1' }), ln({ name: 'l2', bus1: 'Q' })],
    })
    const rect = (o: typeof objects[number]) => ({
      x0: o.origin[0] - o.footprint[0] / 2, x1: o.origin[0] + o.footprint[0] / 2,
      y0: o.origin[1] - o.footprint[1] / 2, y1: o.origin[1] + o.footprint[1] / 2,
    })
    for (let i = 0; i < objects.length; i++) {
      const a = rect(objects[i])
      expect(Math.max(Math.abs(a.x0), Math.abs(a.x1), Math.abs(a.y0), Math.abs(a.y1))).toBeLessThanOrEqual(halfSizeM)
      for (let j = i + 1; j < objects.length; j++) {
        const b = rect(objects[j])
        const overlap = a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1
        expect(overlap, `${objects[i].name} overlaps ${objects[j].name}`).toBe(false)
      }
    }
  })

  it('is deterministic', () => {
    const input = { ...empty, generators: [gen({}), gen({ name: 'h', carrier: 'solar' })], storageUnits: [su({})] }
    expect(buildSiteLayout(input)).toEqual(buildSiteLayout(input))
  })
})
