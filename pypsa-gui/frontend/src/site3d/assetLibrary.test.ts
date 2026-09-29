// Phase 2 plan Tasks 1.1–1.2: the asset library as data, and matching.
import { describe, it, expect } from 'vitest'
import { DEFAULT_LIBRARY, validateLibrary, matchType, legendFor, type AssetType, type MatchContext, type MatchComponent } from './assetLibrary'
import { PLACEABLE_CLASSES } from './types'
import { PALETTE_ITEM_IDS, paletteDefaults, type PortFilter } from '../layout/paletteData'

const clone = (lib: readonly AssetType[]): AssetType[] => lib.map(t => ({ ...t, match: t.match.map(m => ({ ...m })), geometry: { ...t.geometry, params: { ...t.geometry.params } } }))

describe('DEFAULT_LIBRARY', () => {
  it('validates and is deep-frozen', () => {
    expect(() => validateLibrary(DEFAULT_LIBRARY)).not.toThrow()
    expect(Object.isFrozen(DEFAULT_LIBRARY)).toBe(true)
    for (const t of DEFAULT_LIBRARY) {
      expect(Object.isFrozen(t), t.id).toBe(true)
      expect(Object.isFrozen(t.match), t.id).toBe(true)
      expect(Object.isFrozen(t.geometry.params), t.id).toBe(true)
    }
  })

  it('has exactly one fallback rule per placeable class, owned by the last type matching that class', () => {
    for (const cls of PLACEABLE_CLASSES) {
      const typesForCls = DEFAULT_LIBRARY.filter(t => t.match.some(m => m.cls === cls))
      const fallbacks = typesForCls.flatMap(t => t.match.filter(m => m.cls === cls && !m.carrier && !m.farCarrier && !m.hasBus2 && !m.port).map(() => t.id))
      expect(fallbacks, cls).toHaveLength(1)
      expect(fallbacks[0], cls).toBe(typesForCls[typesForCls.length - 1].id)
    }
  })

  it('keeps the Phase 1 ids for the types that did not change', () => {
    const ids = new Set(DEFAULT_LIBRARY.map(t => t.id))
    for (const id of ['switchyard', 'transformer', 'feeder', 'bess', 'pv', 'wind', 'electrolyser', 'h2store', 'load', 'thermal', 'store']) expect(ids.has(id), id).toBe(true)
  })

  it('has 17 asset types and 4 infrastructure types', () => {
    const infra = DEFAULT_LIBRARY.filter(t => t.flags?.infrastructure).map(t => t.id).sort()
    expect(infra).toEqual(['feeder', 'manifold', 'switchyard', 'transformer'])
    // The generic energy store is the Store class's fallback, not one of the 17.
    expect(DEFAULT_LIBRARY.filter(t => !t.flags?.infrastructure && t.id !== 'store')).toHaveLength(17)
  })
})

describe('hero models', () => {
  it('the types that look like a hero model name it, and only known models', () => {
    const heroOf = Object.fromEntries(DEFAULT_LIBRARY.filter(t => t.hero).map(t => [t.id, t.hero]))
    expect(heroOf).toEqual({
      pvRoof: 'pvTable', pv: 'pvTable', wind: 'turbine', thermal: 'container', h2store: 'tank', bess: 'container',
      electrolyser: 'container', fuelCell: 'container', heatPump: 'container', load: 'hall',
    })
  })
  it('refuses an unknown hero', () => {
    const lib = clone(DEFAULT_LIBRARY); (lib.find(t => t.id === 'bess') as { hero?: string }).hero = 'teapot'
    expect(() => validateLibrary(lib)).toThrow(/bess.*hero/)
  })
})

describe('validateLibrary', () => {
  const bad = (edit: (lib: AssetType[]) => void) => { const lib = clone(DEFAULT_LIBRARY); edit(lib); return () => validateLibrary(lib) }
  it('rejects a zero or non-finite number, naming the field', () => {
    expect(bad(lib => { lib.find(t => t.id === 'bess')!.geometry.params.per = 0 })).toThrow(/bess.*per/)
    expect(bad(lib => { lib.find(t => t.id === 'wind')!.geometry.params.hubHeight = NaN })).toThrow(/wind.*hubHeight/)
  })
  it('rejects a duplicate id', () => {
    expect(bad(lib => { lib.push({ ...lib[0] }) })).toThrow(/duplicate id/)
  })
  it('rejects a class with no fallback, and a class with two', () => {
    expect(bad(lib => { const t = lib.find(x => x.id === 'transformer')!; t.match = [{ cls: 'Transformer', carrier: /^x$/i }] })).toThrow(/Transformer.*fallback/)
    expect(bad(lib => { lib.find(x => x.id === 'feeder')!.match = [...lib.find(x => x.id === 'feeder')!.match, { cls: 'Transformer' }] })).toThrow(/Transformer.*fallback/)
  })
  it('rejects an unknown template', () => {
    expect(bad(lib => { (lib[0].geometry as { template: string }).template = 'teapot' })).toThrow(/template/)
  })
  it('rejects a regex with the g or y flag (a frozen stateful regex throws on .test)', () => {
    expect(bad(lib => { lib.find(t => t.id === 'wind')!.match = [{ cls: 'Generator', carrier: /wind/gi }] })).toThrow(/flag/)
    expect(bad(lib => { lib.find(t => t.id === 'wind')!.match = [{ cls: 'Generator', carrier: /wind/y }] })).toThrow(/flag/)
  })
})

// ── Matching ────────────────────────────────────────────────────────────────

const BUSES: Record<string, string> = { 'AC bus': 'AC', 'H2 bus': 'H2', 'Heat bus': 'heat', 'Gas bus': 'gas' }
const busFor: Record<PortFilter, string> = { electricity: 'AC bus', 'non-h2': 'AC bus', h2: 'H2 bus', heat: 'Heat bus', gas: 'Gas bus' }
const ctx = (members = Object.keys(BUSES), carriers: Record<string, string> = BUSES): MatchContext => ({
  members, busCarrier: name => carriers[name],
})

/** The component the creation form would create for a palette item. */
function paletteComponent(id: string): { cls: string; comp: MatchComponent } {
  const d = paletteDefaults(id)
  const comp: MatchComponent = { name: `${id}-1`, carrier: d.carrier ?? '' }
  if (d.cls === 'Bus') return { cls: d.cls, comp: { name: 'AC bus', carrier: 'AC' } }
  if (d.cls === 'Line' || d.cls === 'Transformer') Object.assign(comp, { bus0: 'AC bus', bus1: 'AC bus 2' })
  else if (d.cls === 'Link') for (const p of ['bus0', 'bus1', 'bus2'] as const) comp[p] = d.ports?.[p] ? busFor[d.ports[p]!] : ''
  else comp.bus = d.ports?.bus ? busFor[d.ports.bus] : 'AC bus'
  return { cls: d.cls, comp }
}

describe('matchType', () => {
  // Every palette item's default component → the type spec §4.2 gives it.
  const EXPECTED: Record<string, string> = {
    bus: 'switchyard', line: 'feeder', transformer: 'transformer',
    thermal: 'thermal', renewable: 'wind',
    electrolyzer: 'electrolyser', fuel_cell: 'fuelCell',
    power_to_heat: 'heatPump', chp: 'chp',
    battery: 'bess', psh: 'pumpedHydro', caes: 'caes', flywheel: 'flywheel', hydrogen: 'h2store', thermal_storage: 'thermalStore',
    load_elec: 'load', load_h2: 'offtake', load_heat: 'offtake',
  }
  it('covers every palette item', () => {
    expect(Object.keys(EXPECTED).sort()).toEqual([...PALETTE_ITEM_IDS].sort())
  })
  it.each(PALETTE_ITEM_IDS)('palette item %s maps to its type', id => {
    const { cls, comp } = paletteComponent(id)
    const members = [...Object.keys(BUSES), 'AC bus 2']
    const m = matchType(cls, comp, ctx(members, { ...BUSES, 'AC bus 2': 'AC' }))
    expect(m?.type.id).toBe(EXPECTED[id])
  })
  it('all three power-to-heat carriers are heat pumps / boilers', () => {
    for (const carrier of ['heat-pump-air', 'heat-pump-ground', 'resistive-heater']) {
      expect(matchType('Link', { name: 'p2h', carrier, bus0: 'AC bus', bus1: 'Heat bus', bus2: '' }, ctx())?.type.id).toBe('heatPump')
    }
  })
  it('a bus is a manifold when it carries H2, heat or gas', () => {
    for (const [name, carrier] of Object.entries(BUSES)) {
      expect(matchType('Bus', { name, carrier }, ctx())?.type.id, name).toBe(carrier === 'AC' ? 'switchyard' : 'manifold')
    }
    expect(matchType('Bus', { name: 'DC bus', carrier: 'DC' }, ctx(['DC bus']))?.type.id).toBe('switchyard')
  })

  it.each([
    ['Generator', 'solar', 'pv'], ['Generator', 'solar-rooftop', 'pvRoof'],
    ['Generator', 'CCGT', 'gasTurbine'], ['Generator', 'OCGT', 'gasTurbine'],
    ['Generator', 'onwind', 'wind'], ['Generator', 'offwind-ac', 'wind'],
    ['Generator', 'diesel', 'thermal'], ['Generator', 'biomass', 'thermal'], ['Generator', 'coal', 'thermal'],
    ['Store', 'battery', 'bess'], ['Store', 'H2', 'h2store'], ['Store', 'heat', 'thermalStore'], ['Store', 'methanol', 'store'],
    ['StorageUnit', 'PHS', 'pumpedHydro'], ['StorageUnit', 'something new', 'bess'],
  ])('%s with carrier %s → %s', (cls, carrier, id) => {
    expect(matchType(cls, { name: 'x', carrier, bus: 'AC bus' }, ctx())?.type.id).toBe(id)
  })

  it('the Generator fallback names its carrier in the label', () => {
    const coal = matchType('Generator', { name: 'x', carrier: 'coal', bus: 'AC bus' }, ctx())!
    const label = typeof coal.type.label === 'function' ? coal.type.label('coal') : coal.type.label
    expect(label).toMatch(/coal/)
  })

  it.each([
    ['datacenter', 'load'], ['DC', 'feeder'], ['H2 pipeline', 'feeder'], ['H2 fuel cell', 'fuelCell'], ['H2 electrolysis', 'electrolyser'],
  ])('Link %s → %s', (carrier, id) => {
    const bus1 = carrier === 'H2 fuel cell' ? 'AC bus' : 'H2 bus'
    const bus0 = carrier === 'H2 fuel cell' ? 'H2 bus' : 'AC bus'
    expect(matchType('Link', { name: 'x', carrier, bus0, bus1, bus2: '' }, ctx())?.type.id).toBe(id)
  })

  it('an H2 link is an electrolyser AC→H2 and a fuel cell H2→AC, drawn from its electrical side', () => {
    const ely = matchType('Link', { name: 'e', carrier: 'H2', bus0: 'AC bus', bus1: 'H2 bus', bus2: '' }, ctx())!
    expect([ely.type.id, ely.owner]).toEqual(['electrolyser', 'AC bus'])
    const fc = matchType('Link', { name: 'f', carrier: 'H2', bus0: 'H2 bus', bus1: 'AC bus', bus2: '' }, ctx())!
    expect([fc.type.id, fc.owner]).toEqual(['fuelCell', 'AC bus'])
  })

  it('an unknown bus carrier counts as AC', () => {
    expect(matchType('Bus', { name: 'mystery', carrier: undefined }, ctx(['mystery'], {}))?.type.id).toBe('switchyard')
    // …and an H2 link to a bus of unknown carrier is not an electrolyser.
    expect(matchType('Link', { name: 'e', carrier: 'H2', bus0: 'AC bus', bus1: 'mystery', bus2: '' }, ctx(['AC bus'], { 'AC bus': 'AC' }))?.type.id).toBe('feeder')
  })

  it('(pin) the component name never affects the match', () => {
    const a = matchType('Generator', { name: 'Solar farm North', carrier: 'gas', bus: 'AC bus' }, ctx())
    const b = matchType('Generator', { name: 'Gas engines', carrier: 'gas', bus: 'AC bus' }, ctx())
    expect(a?.type.id).toBe(b?.type.id)
  })

  it('a component not attached to any member is not matched', () => {
    expect(matchType('Generator', { name: 'x', carrier: 'solar', bus: 'elsewhere' }, ctx())).toBeNull()
    expect(matchType('Line', { name: 'x', bus0: 'P', bus1: 'Q' }, ctx())).toBeNull()
  })
})

describe('carrier patterns (WP1 review gate)', () => {
  const at = (cls: string, carrier: string, extra: Record<string, unknown> = {}) =>
    matchType(cls, { name: 'x', carrier, bus: 'AC bus', bus0: 'AC bus', bus1: 'Heat bus', bus2: '', ...extra }, ctx())?.type.id
  it.each([
    ['Store', 'H2 Store', 'h2store'], ['Store', 'H2 storage', 'h2store'], ['Store', 'hydrogen storage', 'h2store'],
    ['StorageUnit', 'H2 storage', 'h2store'], ['Store', 'H2O', 'store'],
    ['Generator', 'urban central solar thermal', 'thermal'], ['Generator', 'solar-hsat', 'pv'], ['Generator', 'PV', 'pv'],
    ['Load', 'urban central heat', 'offtake'], ['Load', 'H2 for industry', 'offtake'], ['Load', 'electricity', 'load'],
  ])('%s %s → %s', (cls, carrier, id) => { expect(at(cls, carrier)).toBe(id) })
  it('a gas boiler is not a heat pump, and "biogas to gas" with a co2 bus2 is not a CHP', () => {
    expect(at('Link', 'urban central gas boiler', { bus0: 'Gas bus' })).toBe('feeder')
    expect(at('Link', 'biogas to gas', { bus0: 'Gas bus', bus1: 'Gas bus', bus2: 'co2 atmosphere' })).toBe('feeder')
    expect(at('Link', 'electric boiler')).toBe('heatPump')
  })
  it('buses the creation form treats as gas or H₂ are manifolds', () => {
    for (const carrier of ['natural gas', 'biogas', 'H2 pipeline', 'urban central heat', 'oil']) {
      expect(matchType('Bus', { name: 'b', carrier }, ctx(['b'], { b: carrier }))?.type.id, carrier).toBe('manifold')
    }
  })
  it('an H2 link into an H2 pipeline bus is an electrolyser', () => {
    expect(matchType('Link', { name: 'e', carrier: 'H2', bus0: 'AC bus', bus1: 'pipe', bus2: '' }, ctx(['AC bus'], { 'AC bus': 'AC', pipe: 'H2 pipeline' }))?.type.id).toBe('electrolyser')
  })
})

describe('legendFor', () => {
  it('lists the types present, in library order, with label and colour from the entry', () => {
    const objs = [{ kind: 'wind', carrier: 'onwind' }, { kind: 'bess', carrier: 'battery' }, { kind: 'wind', carrier: 'offwind-ac' }, { kind: 'thermal', carrier: 'coal' }]
    const legend = legendFor(objs)
    const order = DEFAULT_LIBRARY.map(t => t.id)
    expect(legend.map(e => e.id)).toEqual(['wind', 'bess', 'thermal'].sort((a, b) => order.indexOf(a) - order.indexOf(b)))
    for (const e of legend) expect(e.color).toBe(DEFAULT_LIBRARY.find(t => t.id === e.id)!.color)
    expect(legend.find(e => e.id === 'thermal')!.label).toMatch(/coal/)
  })
})
