import { describe, it, expect } from 'vitest'
import { buildSiteLayout, objectKey, type SiteInput, type SiteObject } from './layout'
import { DEFAULT_LIBRARY, PACKING, type AssetType } from './assetLibrary'
import { rectOf, rectDistance, rectsOverlap, betweenSegment, segmentRectDistance, bearingDeg, angleDiffDeg } from './placementCheck'
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
  buses: [{ name: 'B', v_nom: 110, offset: [0, 0] }],
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

  it('optimised sizing draws an extendable BESS at p_nom_opt (4× the containers) and says so; installed by default', () => {
    const bess = su({ name: 'BESS 1', p_nom: 10, p_nom_opt: 40, p_nom_extendable: true, max_hours: 4 })
    const count = (o: { summary: string }) => Number(/in (\d+) containers?/.exec(o.summary)?.[1])
    const inst = buildSiteLayout({ ...empty, storageUnits: [bess] }).objects[1]
    const def = buildSiteLayout({ ...empty, storageUnits: [bess], sizing: 'installed' }).objects[1]
    const opt = buildSiteLayout({ ...empty, storageUnits: [bess], sizing: 'optimised' }).objects[1]
    expect(inst.summary).toBe(def.summary)
    expect(inst.summary).not.toMatch(/optimised/)
    expect(count(opt)).toBe(4 * count(inst))
    expect(opt.summary).toMatch(/160 MWh \/ 40 MW .*\(optimised\)$/)
    expect(opt.areaM2).toBeGreaterThan(inst.areaM2)
    // not extendable: optimised mode keeps the installed size and says nothing
    const fixed = buildSiteLayout({ ...empty, storageUnits: [{ ...bess, p_nom_extendable: false }], sizing: 'optimised' }).objects[1]
    expect(fixed.summary).toBe(inst.summary)
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
    // Phase 2 (spec §4.2): CCGT has its own type; every other id is Phase 1's.
    expect(kinds).toMatchObject({ pv: 'pv', w: 'wind', ccgt: 'gasTurbine', h2: 'h2store', ely: 'electrolyser', hvdc: 'feeder' })
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

describe('buildSiteLayout — several buses, placements, rules (WP3)', () => {
  const two: SiteInput = {
    ...empty,
    buses: [{ name: 'B', v_nom: 110, offset: [0, 0] }, { name: 'C', v_nom: 33, offset: [300, -50] }],
    generators: [gen({ name: 'gB', bus: 'B' }), gen({ name: 'gC', bus: 'C', carrier: 'solar', p_nom: 2 })],
    transformers: [tr({ name: 'T', bus0: 'B', bus1: 'C' })],
    lines: [ln({ name: 'L', bus0: 'C', bus1: 'Z' })],
  }

  it('draws one switchyard per member bus at that bus\'s offset and packs each bus\'s assets around its own yard', () => {
    const { objects } = buildSiteLayout(two)
    const yards = objects.filter(o => o.kind === 'switchyard')
    expect(yards.map(y => y.name)).toEqual(['B', 'C'])
    expect(yards[0].origin).toEqual([0, 0])
    expect(yards[1].origin).toEqual([300, -50])
    // C's assets sit exactly where a one-bus layout of C would put them, shifted by C's offset.
    const alone = buildSiteLayout({ ...two, buses: [{ name: 'C', v_nom: 33, offset: [0, 0] }] })
    const gC = objects.find(o => o.name === 'gC')!, gCAlone = alone.objects.find(o => o.name === 'gC')!
    expect(gC.origin[0]).toBeCloseTo(gCAlone.origin[0] + 300, 6)
    expect(gC.origin[1]).toBeCloseTo(gCAlone.origin[1] - 50, 6)
  })

  it('a two-terminal component between two member buses is drawn once, owned by the first member it touches', () => {
    const { objects } = buildSiteLayout(two)
    const ts = objects.filter(o => o.name === 'T')
    expect(ts).toHaveLength(1)
    // bus0 = B is a member → attributed to B; its placement rule puts it
    // between B's yard (x = 0) and C's (x = 300), not in B's north zone (S2).
    expect(ts[0].bus).toBe('B')
    expect(ts[0].origin[0]).toBeGreaterThan(0)
    expect(ts[0].origin[0]).toBeLessThan(300)
    expect(objects.filter(o => o.name === 'L')).toHaveLength(1)
  })

  it('a placement keeps its origin and heading; orphans are reported, never pruned', () => {
    const { objects, orphans, placed } = buildSiteLayout({
      ...two,
      placements: {
        'Generator:gB': { x: -120, y: 40, heading: 45 },
        'Generator:gone': { x: 1, y: 1, heading: 0 },
      },
    })
    const gB = objects.find(o => o.name === 'gB')!
    expect(gB.origin).toEqual([-120, 40])
    expect(gB.heading).toBe(45)
    expect(placed).toEqual(['Generator:gB'])
    expect(orphans).toEqual(['Generator:gone'])
  })

  it('a placement for the bus moves its yard, and its unplaced assets follow it', () => {
    const base = buildSiteLayout(two)
    const moved = buildSiteLayout({ ...two, placements: { 'Bus:C': { x: 600, y: 200, heading: 0 } } })
    const yardC = moved.objects.find(o => o.kind === 'switchyard' && o.name === 'C')!
    expect(yardC.origin).toEqual([600, 200])
    const gC0 = base.objects.find(o => o.name === 'gC')!
    const gC1 = moved.objects.find(o => o.name === 'gC')!
    expect(gC1.origin[0] - gC0.origin[0]).toBeCloseTo(300, 6)
    expect(gC1.origin[1] - gC0.origin[1]).toBeCloseTo(250, 6)
    // B's assets did not move.
    expect(moved.objects.find(o => o.name === 'gB')!.origin).toEqual(base.objects.find(o => o.name === 'gB')!.origin)
    // A PLACED asset on the moved bus stays where the user put it.
    const pinned = buildSiteLayout({ ...two, placements: { 'Bus:C': { x: 600, y: 200, heading: 0 }, 'Generator:gC': { x: -5, y: -5, heading: 10 } } })
    expect(pinned.objects.find(o => o.name === 'gC')!.origin).toEqual([-5, -5])
  })

  it('a line whose bus1 is the member is drawn at that member, labelled with the far bus', () => {
    const { objects } = buildSiteLayout({ ...empty, lines: [ln({ name: 'L', bus0: 'FAR', bus1: 'B' })] })
    const l = objects.find(o => o.name === 'L')!
    expect(l.summary).toContain('to FAR')
    // An electrolyser link seen from its hydrogen side is a feeder, not an electrolyser.
    const h2 = buildSiteLayout({ ...empty, links: [lk({ name: 'ely', carrier: 'electrolysis', bus0: 'ELEC', bus1: 'B' })] })
    expect(h2.objects.find(o => o.name === 'ely')!.kind).toBe('feeder')
    expect(h2.objects.find(o => o.name === 'ely')!.summary).toContain('to ELEC')
  })

  it('objectKey is the placement key', () => {
    expect(objectKey({ type: 'Generator', name: 'gB' })).toBe('Generator:gB')
  })

  it('the library drives the geometry: halving MWh per container doubles the container count and nothing else', () => {
    // Phase 2 (plan Task 1.3): the numbers live in the library entries.
    const halfBess = (lib: readonly AssetType[]): AssetType[] => lib.map(t => t.id !== 'bess' ? t : { ...t, geometry: { ...t.geometry, params: { ...t.geometry.params, per: 2 } } })
    const input = { ...empty, storageUnits: [su({ p_nom: 10, max_hours: 4 })] }
    const a = buildSiteLayout(input).objects[1]
    const b = buildSiteLayout({ ...input, library: halfBess(DEFAULT_LIBRARY) }).objects[1]
    expect(a.summary).toContain('10 containers')
    expect(b.summary).toContain('20 containers')
    const c = buildSiteLayout({ ...empty, generators: [gen({ carrier: 'solar', p_nom: 4 })], library: halfBess(DEFAULT_LIBRARY) }).objects[1]
    const c0 = buildSiteLayout({ ...empty, generators: [gen({ carrier: 'solar', p_nom: 4 })] }).objects[1]
    expect(c.areaM2).toBe(c0.areaM2)
  })

  it('an invalid library is refused with the field named', () => {
    const lib = DEFAULT_LIBRARY.map(t => t.id !== 'wind' ? t : { ...t, geometry: { ...t.geometry, params: { ...t.geometry.params, per: 0 } } })
    expect(() => buildSiteLayout({ ...empty, library: lib })).toThrow(/wind.*per/)
  })

  it('is deterministic with placements and two buses', () => {
    const input = { ...two, placements: { 'Generator:gB': { x: 1, y: 2, heading: 3 } } }
    expect(buildSiteLayout(input)).toEqual(buildSiteLayout(input))
  })

  // ── Phase 2: owner after match, three-port links, rooftop PV (plan Tasks 1.4–1.5) ──

  const carriers: Record<string, string> = { B: 'AC', H: 'H2', Q: 'heat', G: 'gas' }
  const multi = (buses: string[], extra: Partial<SiteInput> = {}): SiteInput => ({
    ...empty, ...extra,
    buses: buses.map((name, i) => ({ name, v_nom: carriers[name] === 'AC' ? 110 : 0, offset: [i * 300, 0] as [number, number] })),
    busCarrier: (n: string) => carriers[n],
  })

  it('an electrolyser with both its AC and H₂ buses as members is one object, drawn from the AC bus', () => {
    const { objects } = buildSiteLayout(multi(['H', 'B'], { links: [lk({ name: 'ely', carrier: 'H2', bus0: 'B', bus1: 'H' })] }))
    const ely = objects.filter(o => o.name === 'ely')
    expect(ely).toHaveLength(1)
    expect([ely[0].kind, ely[0].bus]).toEqual(['electrolyser', 'B'])
  })

  it('a fuel cell is drawn from its electrical side (bus1), once', () => {
    const { objects } = buildSiteLayout(multi(['H', 'B'], { links: [lk({ name: 'fc', carrier: 'H2', bus0: 'H', bus1: 'B' })] }))
    const fc = objects.filter(o => o.name === 'fc')
    expect(fc).toHaveLength(1)
    expect([fc[0].kind, fc[0].bus]).toEqual(['fuelCell', 'B'])
  })

  it('an electrolyser whose H₂ bus is not a member is still drawn, from the AC bus', () => {
    const { objects } = buildSiteLayout(multi(['B'], { links: [lk({ name: 'ely', carrier: 'H2', bus0: 'B', bus1: 'H' })] }))
    expect(objects.find(o => o.name === 'ely')?.kind).toBe('electrolyser')
  })

  it('a CHP is drawn once: from its electrical port when that is a member, else as a feeder from the member it touches', () => {
    const chp = lk({ name: 'chp', carrier: 'gas', bus0: 'G', bus1: 'B', bus2: 'Q' })
    const all = buildSiteLayout(multi(['G', 'B', 'Q'], { links: [chp] })).objects.filter(o => o.name === 'chp')
    expect(all).toHaveLength(1)
    expect([all[0].kind, all[0].bus]).toEqual(['chp', 'B'])
    const heatOnly = buildSiteLayout(multi(['Q'], { links: [chp] })).objects.filter(o => o.name === 'chp')
    expect(heatOnly).toHaveLength(1)
    expect([heatOnly[0].kind, heatOnly[0].bus]).toEqual(['feeder', 'Q'])
  })

  it('an empty-string bus2 counts as absent', () => {
    const { objects } = buildSiteLayout(multi(['B'], { links: [lk({ name: 'g', carrier: 'gas', bus0: 'G', bus1: 'B', bus2: '' })] }))
    expect(objects.find(o => o.name === 'g')?.kind).toBe('feeder')
  })

  it('a branch knows its owner and its far bus', () => {
    const { objects } = buildSiteLayout({ ...empty, lines: [ln({ name: 'L', bus0: 'FAR', bus1: 'B' })] })
    const l = objects.find(o => o.name === 'L')!
    expect([l.bus, l.far]).toEqual(['B', 'FAR'])
  })

  it('an H₂ bus member is a manifold, an AC bus a switchyard', () => {
    const { objects } = buildSiteLayout(multi(['B', 'H']))
    expect(objects.filter(o => o.type === 'Bus').map(o => [o.name, o.kind])).toEqual([['B', 'switchyard'], ['H', 'manifold']])
  })

  it('rooftop PV in optimised mode: the rooftop pass sizes it at the optimum too', () => {
    const input = multi(['B', 'Q'], {
      loads: [ld({ name: 'hall', bus: 'B', p_set: 20 })],
      generators: [gen({ name: 'roof', bus: 'B', carrier: 'solar-rooftop', p_nom: 0.1, p_nom_opt: 0.4, p_nom_extendable: true })],
    })
    const inst = buildSiteLayout(input).objects.find(o => o.name === 'roof')!
    const opt = buildSiteLayout({ ...input, sizing: 'optimised' }).objects.find(o => o.name === 'roof')!
    expect(opt.elevation).toBeGreaterThan(0)          // still on the roof
    expect(opt.summary).toMatch(/0\.4 MW.*\(optimised\)$/)
    expect(inst.summary).toMatch(/0\.1 MW/)
    expect(opt.parts.length).toBeGreaterThan(inst.parts.length)
  })

  it('rooftop PV sits on the largest data hall, follows the hall\'s placement, and takes no land', () => {
    const input = multi(['B', 'Q'], {
      loads: [ld({ name: 'hall-small', bus: 'B', p_set: 2 }), ld({ name: 'hall-big', bus: 'B', p_set: 20 })],
      generators: [gen({ name: 'roof', bus: 'B', carrier: 'solar-rooftop', p_nom: 1 })],
    })
    const packed = buildSiteLayout(input)
    const hall = packed.objects.find(o => o.name === 'hall-big')!
    const roof = packed.objects.find(o => o.name === 'roof')!
    expect(roof.kind).toBe('pvRoof')
    expect(roof.origin).toEqual(hall.origin)
    expect(roof.elevation).toBeGreaterThan(0)
    expect(roof.areaM2).toBe(0)
    const moved = buildSiteLayout({ ...input, placements: { 'Load:hall-big': { x: 500, y: -200, heading: 30 } } })
    const roof2 = moved.objects.find(o => o.name === 'roof')!
    expect(roof2.origin).toEqual([500, -200])
    expect(roof2.heading).toBe(30)
    // Exempt from the no-overlap rule (it is on the roof), and fit.ts land sum excludes it (areaM2 0).
  })

  it('rooftop PV fits the free part of the roof, clear of the rooftop plant', () => {
    const { objects } = buildSiteLayout(multi(['B'], {
      loads: [ld({ name: 'hall', bus: 'B', p_set: 1 })],
      generators: [gen({ name: 'roof', bus: 'B', carrier: 'solar-rooftop', p_nom: 1 })],
    }))
    const hall = objects.find(o => o.name === 'hall')!, roof = objects.find(o => o.name === 'roof')!
    expect(roof.elevation).toBeGreaterThan(0)
    expect(roof.footprint[0]).toBeLessThanOrEqual(hall.footprint[0])
    expect(roof.footprint[1]).toBeLessThanOrEqual(hall.footprint[1])
    const [w, d] = hall.footprint
    for (const p of roof.parts) {
      expect(Math.abs(p.pos[0]) + p.size[0] / 2).toBeLessThanOrEqual(w / 2)
      expect(p.pos[1] + p.size[1] / 2).toBeLessThanOrEqual(0.15 * d)   // south of the plant strip
      expect(p.pos[1] - p.size[1] / 2).toBeGreaterThanOrEqual(-d / 2)
    }
    expect(roof.summary).toMatch(/each table = \d+|Rooftop PV/)
  })

  it('rooftop PV on a site with a hall leaves no hole in the south zone', () => {
    const withRoof = buildSiteLayout(multi(['B'], {
      loads: [ld({ name: 'hall', bus: 'B', p_set: 5 })],
      generators: [gen({ name: 'roof', bus: 'B', carrier: 'solar-rooftop', p_nom: 1 }), gen({ name: 'field', bus: 'B', carrier: 'solar', p_nom: 1 })],
    }))
    const without = buildSiteLayout(multi(['B'], {
      loads: [ld({ name: 'hall', bus: 'B', p_set: 5 })],
      generators: [gen({ name: 'field', bus: 'B', carrier: 'solar', p_nom: 1 })],
    }))
    expect(withRoof.objects.find(o => o.name === 'field')!.origin).toEqual(without.objects.find(o => o.name === 'field')!.origin)
  })

  it('rooftop PV without a hall stands on a canopy in the south zone', () => {
    const { objects } = buildSiteLayout({ ...empty, generators: [gen({ name: 'roof', carrier: 'solar-rooftop', p_nom: 1 })] })
    const roof = objects.find(o => o.name === 'roof')!
    expect(roof.origin[1]).toBeLessThan(0)
    expect(roof.elevation ?? 0).toBe(0)
    expect(roof.parts.some(p => p.pos[2] >= 4)).toBe(true)
  })

  it('switchyard width counts bays from the library flags, not kind names', () => {
    const one = buildSiteLayout({ ...empty, lines: Array.from({ length: 5 }, (_, i) => ln({ name: `L${i}` })) })
    expect(one.objects[0].footprint[0]).toBe(12 + 5 * 8)
  })
})

// ── S2: placement that is plausible (visual-layers plan 3, owner decision O1) ──

describe('buildSiteLayout — placement rules (S2)', () => {
  // A campus: a 110 kV grid yard west, the 33 kV campus yard 400 m east, two
  // transformers between them, and the campus plant at the 33 kV bus.
  const campus: SiteInput = {
    ...empty,
    buses: [{ name: 'HV', v_nom: 110, offset: [0, 0] }, { name: 'MV', v_nom: 33, offset: [400, 0] }],
    transformers: [tr({ name: 'TR1', bus0: 'HV', bus1: 'MV' }), tr({ name: 'TR2', bus0: 'HV', bus1: 'MV' })],
    storageUnits: [su({ name: 'BESS', bus: 'MV', p_nom: 10, max_hours: 4 })],
    generators: [gen({ name: 'GEN', bus: 'MV', carrier: 'gas', p_nom: 20 }), gen({ name: 'PV', bus: 'MV', carrier: 'solar', p_nom: 2 })],
    loads: [ld({ name: 'HALL', bus: 'MV', p_set: 30 })],
    stores: [st({ name: 'H2', bus: 'MV', carrier: 'H2', e_nom: 100 })],
    lines: [ln({ name: 'GRID', bus0: 'HV', bus1: 'EXT' })],
  }
  const find = (objects: SiteObject[], name: string) => objects.find(o => o.name === name)!
  const yardOf = (objects: SiteObject[], bus: string) => objects.find(o => o.type === 'Bus' && o.name === bus)!

  it('a transformer between two member yards lands on the segment between them, within tolerance, and faces the lower-voltage bus', () => {
    const { objects } = buildSiteLayout(campus)
    const seg = betweenSegment(rectOf(yardOf(objects, 'HV')), rectOf(yardOf(objects, 'MV')))!
    expect(seg).not.toBeNull()
    for (const name of ['TR1', 'TR2']) {
      const t = find(objects, name)
      expect(t.bus).toBe('HV')
      // The inter-yard line passes through (or within tolerance of) its footprint: two transformers straddle it.
      expect(segmentRectDistance(seg.a, seg.b, rectOf(t)), name).toBeLessThanOrEqual(PACKING.betweenToleranceM)
      // Clear of both yards, on the segment's span.
      expect(t.origin[0]).toBeGreaterThan(seg.a[0])
      expect(t.origin[0]).toBeLessThan(seg.b[0])
      // Facing MV (33 kV), due east of it: a heading of 90°, within the facing tolerance.
      expect(angleDiffDeg(t.heading, bearingDeg(t.origin, yardOf(objects, 'MV').origin)), name).toBeLessThanOrEqual(PACKING.facingToleranceDeg)
      expect(angleDiffDeg(t.heading, 90), name).toBeLessThan(5)
    }
    // Two transformers are two objects, side by side, not one on top of the other.
    expect(rectsOverlap(rectOf(find(objects, 'TR1')), rectOf(find(objects, 'TR2')))).toBe(false)
    expect(find(objects, 'TR1').origin).not.toEqual(find(objects, 'TR2').origin)
  })

  it('a transformer whose far bus is not a member falls back to its owner\'s yard zone, facing north', () => {
    const { objects } = buildSiteLayout({ ...empty, transformers: [tr({ name: 'T', bus0: 'B', bus1: 'ELSEWHERE' })] })
    const t = find(objects, 'T'), yard = yardOf(objects, 'B')
    expect(t.heading).toBe(0)
    // North zone: above the yard's north edge.
    expect(t.origin[1] - t.footprint[1] / 2).toBeGreaterThanOrEqual(yard.origin[1] + yard.footprint[1] / 2)
  })

  it('a BESS packs adjacent to a transformer of its bus (one gap away, touching nothing else)', () => {
    const { objects } = buildSiteLayout(campus)
    const bess = find(objects, 'BESS')
    const d = Math.min(rectDistance(rectOf(bess), rectOf(find(objects, 'TR1'))), rectDistance(rectOf(bess), rectOf(find(objects, 'TR2'))))
    expect(d).toBeLessThanOrEqual(PACKING.gapM + 1e-6)
    expect(d).toBeGreaterThan(0)
  })

  it('a BESS without a transformer sits next to its yard', () => {
    const { objects } = buildSiteLayout({ ...empty, storageUnits: [su({ name: 'BESS' })] })
    expect(rectDistance(rectOf(find(objects, 'BESS')), rectOf(yardOf(objects, 'B')))).toBeLessThanOrEqual(PACKING.gapM + 1e-6)
  })

  it('a genset is never inside a hall\'s keep-out after packing', () => {
    const { objects } = buildSiteLayout(campus)
    const m = DEFAULT_LIBRARY.find(t => t.id === 'thermal')!.placement!.keepOutM![0].m
    expect(rectDistance(rectOf(find(objects, 'GEN')), rectOf(find(objects, 'HALL')))).toBeGreaterThanOrEqual(m)
    // The same when the hall is packed first (it owns the bus's northeast zone; the genset the west zone) — the rule is symmetric.
    const west = buildSiteLayout({ ...campus, loads: [ld({ name: 'HALL', bus: 'MV', p_set: 300 })] }).objects
    expect(rectDistance(rectOf(find(west, 'GEN')), rectOf(find(west, 'HALL')))).toBeGreaterThanOrEqual(m)
  })

  it('H₂ storage keeps its distance from everything', () => {
    const { objects } = buildSiteLayout(campus)
    const m = DEFAULT_LIBRARY.find(t => t.id === 'h2store')!.placement!.keepOutM![0].m
    const h2 = find(objects, 'H2')
    for (const o of objects) {
      if (o === h2 || o.elevation) continue
      expect(rectDistance(rectOf(h2), rectOf(o)), o.name).toBeGreaterThanOrEqual(m)
    }
  })

  it('PV keeps its clearance from everything', () => {
    const { objects } = buildSiteLayout(campus)
    const m = DEFAULT_LIBRARY.find(t => t.id === 'pv')!.placement!.clearanceM!
    const pv = find(objects, 'PV')
    for (const o of objects) {
      if (o === pv || o.elevation) continue
      expect(rectDistance(rectOf(pv), rectOf(o)), o.name).toBeGreaterThanOrEqual(m)
    }
  })

  it('nothing overlaps anything across the whole site (rotated footprints), and every rule was satisfied', () => {
    const layout = buildSiteLayout(campus)
    expect(layout.unresolved).toEqual([])
    const solid = layout.objects.filter(o => !o.elevation)
    for (let i = 0; i < solid.length; i++) for (let j = i + 1; j < solid.length; j++) {
      expect(rectsOverlap(rectOf(solid[i]), rectOf(solid[j])), `${solid[i].name} overlaps ${solid[j].name}`).toBe(false)
    }
  })

  it('is deterministic', () => {
    expect(buildSiteLayout(campus)).toEqual(buildSiteLayout(campus))
  })

  it('placed objects are left exactly where the user put them, rules or not, and the rest arrange around them', () => {
    const hall = find(buildSiteLayout(campus).objects, 'HALL')
    // The genset dropped right beside the hall (inside its keep-out), the transformer far off its segment.
    const placements = {
      'Generator:GEN': { x: hall.origin[0] + hall.footprint[0] / 2 + 5, y: hall.origin[1], heading: 17 },
      'Transformer:TR1': { x: 200, y: 180, heading: 45 },
    }
    const layout = buildSiteLayout({ ...campus, placements })
    expect(find(layout.objects, 'GEN').origin).toEqual([placements['Generator:GEN'].x, placements['Generator:GEN'].y])
    expect(find(layout.objects, 'GEN').heading).toBe(17)
    expect(find(layout.objects, 'TR1').origin).toEqual([200, 180])
    expect(find(layout.objects, 'TR1').heading).toBe(45)
    expect(layout.placed.sort()).toEqual(['Generator:GEN', 'Transformer:TR1'])
    // The unplaced transformer still takes its segment; the BESS still finds one.
    const seg = betweenSegment(rectOf(yardOf(layout.objects, 'HV')), rectOf(yardOf(layout.objects, 'MV')))!
    expect(segmentRectDistance(seg.a, seg.b, rectOf(find(layout.objects, 'TR2')))).toBeLessThanOrEqual(PACKING.betweenToleranceM)
  })

  it('a rule the packer cannot satisfy within its reach is reported, not silently dropped', () => {
    // A library where H₂ storage must keep a kilometre from everything: no
    // slot within the packer's reach. The rule is symmetric, so what is
    // packed after it cannot keep its distance from the stranded store either.
    const lib = DEFAULT_LIBRARY.map(t => t.id !== 'h2store' ? t : { ...t, placement: { keepOutM: [{ from: ['*'], m: 1_000 }] } })
    const layout = buildSiteLayout({ ...campus, library: lib })
    expect(layout.unresolved).toContain('Store:H2')
    expect(find(layout.objects, 'H2').origin).toBeDefined()   // still drawn, where the packer gave up
    expect(buildSiteLayout(campus).unresolved).toEqual([])
  })

  it('a feeder faces its far bus when that bus is a member', () => {
    const { objects } = buildSiteLayout({ ...campus, lines: [ln({ name: 'TIE', bus0: 'MV', bus1: 'HV' })] })
    const tie = find(objects, 'TIE')
    expect(tie.bus).toBe('MV')
    expect(angleDiffDeg(tie.heading, bearingDeg(tie.origin, yardOf(objects, 'HV').origin))).toBeLessThanOrEqual(PACKING.facingToleranceDeg)
  })

  it('the Phase 1/2 invariants still hold on the campus: part counts, land, orphans', () => {
    const layout = buildSiteLayout({ ...campus, placements: { 'Generator:gone': { x: 0, y: 0, heading: 0 } } })
    expect(find(layout.objects, 'BESS').parts.length).toBe(10 + 2)
    expect(find(layout.objects, 'PV').areaM2).toBeCloseTo(50_000, -2)
    expect(layout.totalAreaM2).toBe(layout.objects.reduce((a, o) => a + o.areaM2, 0))
    expect(layout.orphans).toEqual(['Generator:gone'])
    expect(layout.objects.filter(o => o.type === 'Bus').map(o => o.name)).toEqual(['HV', 'MV'])
  })
})

