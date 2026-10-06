// Visual-layers plan 3 S1: the asset taxonomy the schematic, the map and the
// 3D site view share. The palette-driven table mirrors the one in
// site3d/assetLibrary.test.ts so a palette change is seen from both sides.
import { describe, it, expect } from 'vitest'
import {
  ASSET_ICON_NAMES, ASSET_TYPES, ASSET_TYPE_IDS, PYPSA_CLASSES, assetTypeOf, iconNameOf, isFallbackRule, labelOf, legendFor, matchType, validateAssetTypes,
  type AssetTypeSpec, type MatchComponent, type MatchContext,
} from './assetTypes'
import { PALETTE_ITEM_IDS, paletteDefaults, type PortFilter } from '../layout/paletteData'

const clone = (types: readonly AssetTypeSpec[]): AssetTypeSpec[] => types.map(t => ({ ...t, match: t.match.map(m => ({ ...m })) }))

describe('ASSET_TYPES', () => {
  it('validates and is deep-frozen', () => {
    expect(() => validateAssetTypes(ASSET_TYPES)).not.toThrow()
    expect(Object.isFrozen(ASSET_TYPES)).toBe(true)
    for (const t of ASSET_TYPES) {
      expect(Object.isFrozen(t), t.id).toBe(true)
      expect(Object.isFrozen(t.match), t.id).toBe(true)
    }
  })

  it('keeps the ids the 3D view keys placements, legend rows and tests on', () => {
    expect([...ASSET_TYPE_IDS]).toEqual([
      'pvRoof', 'pv', 'wind', 'gasTurbine', 'thermal',
      'flywheel', 'pumpedHydro', 'caes', 'h2store', 'thermalStore', 'bess', 'store',
      'electrolyser', 'fuelCell', 'heatPump', 'chp',
      'offtake', 'load',
      'transformer', 'feeder', 'manifold', 'switchyard',
    ])
  })

  it('ids are unique', () => {
    expect(new Set(ASSET_TYPE_IDS).size).toBe(ASSET_TYPE_IDS.length)
  })

  it('has exactly one fallback rule per class, owned by the last type matching that class', () => {
    for (const cls of PYPSA_CLASSES) {
      const typesForCls = ASSET_TYPES.filter(t => t.match.some(m => m.cls === cls))
      const fallbacks = typesForCls.flatMap(t => t.match.filter(m => m.cls === cls && isFallbackRule(m)).map(() => t.id))
      expect(fallbacks, cls).toHaveLength(1)
      expect(fallbacks[0], cls).toBe(typesForCls[typesForCls.length - 1].id)
    }
  })

  it('each type has a known icon name and a hex colour', () => {
    for (const t of ASSET_TYPES) {
      expect(ASSET_ICON_NAMES, t.id).toContain(t.icon)
      expect(t.color, t.id).toMatch(/^#[0-9a-f]{6}$/i)
      expect(iconNameOf(t.id)).toBe(t.icon)
    }
  })

  it('a palette item id used as an icon name is a real palette item', () => {
    const paletteNames = ASSET_ICON_NAMES.filter(n => !['sun', 'box', 'factory', 'waypoints'].includes(n))
    for (const n of paletteNames) expect(PALETTE_ITEM_IDS, n).toContain(n)
  })

  it('looks a type up by id, and labels it', () => {
    expect(assetTypeOf('bess')?.color).toBe('#7c3aed')
    expect(assetTypeOf('teapot')).toBeUndefined()
    expect(iconNameOf('teapot')).toBeUndefined()
    expect(labelOf(assetTypeOf('wind')!, 'onwind')).toBe('Wind turbines')
    expect(labelOf(assetTypeOf('thermal')!, 'gas')).toBe('Engine gensets (gas)')
    expect(labelOf(assetTypeOf('thermal')!, 'coal')).toMatch(/coal/)
  })
})

describe('validateAssetTypes', () => {
  const bad = (edit: (types: AssetTypeSpec[]) => void) => { const types = clone(ASSET_TYPES); edit(types); return () => validateAssetTypes(types) }
  it('rejects a duplicate id', () => {
    expect(bad(types => { types.push({ ...types[0] }) })).toThrow(/duplicate id/)
  })
  it('rejects an unknown icon name', () => {
    expect(bad(types => { (types[0] as { icon: string }).icon = 'teapot' })).toThrow(/pvRoof.*icon/)
  })
  it('rejects a class with no fallback, and a class with two', () => {
    expect(bad(types => { types.find(x => x.id === 'transformer')!.match = [{ cls: 'Transformer', carrier: /^x$/i }] })).toThrow(/Transformer.*fallback/)
    expect(bad(types => { const f = types.find(x => x.id === 'feeder')!; f.match = [...f.match, { cls: 'Transformer' }] })).toThrow(/Transformer.*fallback/)
  })
  it('rejects a fallback that is not the last type matching its class', () => {
    expect(bad(types => { types.find(x => x.id === 'pvRoof')!.match = [{ cls: 'Generator' }]; types.find(x => x.id === 'thermal')!.match = [{ cls: 'Generator', carrier: /gas/i }] })).toThrow(/Generator.*last/)
  })
  it('rejects a regex with the g or y flag (a frozen stateful regex throws on .test)', () => {
    expect(bad(types => { types.find(t => t.id === 'wind')!.match = [{ cls: 'Generator', carrier: /wind/gi }] })).toThrow(/flag/)
    expect(bad(types => { types.find(t => t.id === 'wind')!.match = [{ cls: 'Generator', carrier: /wind/y }] })).toThrow(/flag/)
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

describe('matchType: every palette item', () => {
  // Every palette item's default component → the type the 3D spec §4.2 gives it.
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
})

describe('matchType: precedence', () => {
  it('the first type in table order wins: a rooftop solar generator is pvRoof, not pv', () => {
    expect(matchType('Generator', { name: 'x', carrier: 'solar-rooftop', bus: 'AC bus' }, ctx())?.type.id).toBe('pvRoof')
    expect(matchType('Generator', { name: 'x', carrier: 'solar', bus: 'AC bus' }, ctx())?.type.id).toBe('pv')
    // …and the fallback only when nothing before it matched.
    expect(matchType('Generator', { name: 'x', carrier: 'coal', bus: 'AC bus' }, ctx())?.type.id).toBe('thermal')
  })

  it('an H2 link is an electrolyser AC→H2 and a fuel cell H2→AC, by the far carrier, drawn from its electrical side', () => {
    const ely = matchType('Link', { name: 'e', carrier: 'H2', bus0: 'AC bus', bus1: 'H2 bus', bus2: '' }, ctx())!
    expect([ely.type.id, ely.owner, ely.far]).toEqual(['electrolyser', 'AC bus', 'H2 bus'])
    const fc = matchType('Link', { name: 'f', carrier: 'H2', bus0: 'H2 bus', bus1: 'AC bus', bus2: '' }, ctx())!
    expect([fc.type.id, fc.owner, fc.far]).toEqual(['fuelCell', 'AC bus', 'H2 bus'])
    // Seen only from the H₂ side (the electrical bus is not a member) it is a feeder.
    expect(matchType('Link', { name: 'e', carrier: 'H2', bus0: 'AC bus', bus1: 'H2 bus', bus2: '' }, ctx(['H2 bus']))?.type.id).toBe('feeder')
  })

  it('a gas link is a CHP only with a bus2; without one it is a feeder', () => {
    const chp = matchType('Link', { name: 'c', carrier: 'gas', bus0: 'Gas bus', bus1: 'AC bus', bus2: 'Heat bus' }, ctx())!
    expect([chp.type.id, chp.owner]).toEqual(['chp', 'AC bus'])
    expect(matchType('Link', { name: 'c', carrier: 'gas', bus0: 'Gas bus', bus1: 'AC bus', bus2: '' }, ctx())?.type.id).toBe('feeder')
  })

  it('a port rule only matches when that port is a member; otherwise the owner is the first member port', () => {
    const line = matchType('Line', { name: 'l', bus0: 'elsewhere', bus1: 'AC bus' }, ctx())!
    expect([line.type.id, line.owner, line.far]).toEqual(['feeder', 'AC bus', 'elsewhere'])
    expect(matchType('Generator', { name: 'x', carrier: 'solar', bus: 'elsewhere' }, ctx())).toBeNull()
  })

  it('an unknown bus carrier counts as AC', () => {
    expect(matchType('Bus', { name: 'mystery', carrier: undefined }, ctx(['mystery'], {}))?.type.id).toBe('switchyard')
    expect(matchType('Bus', { name: 'H2 bus', carrier: 'H2' }, ctx())?.type.id).toBe('manifold')
  })

  it('matches against a caller-supplied table, returning its entries', () => {
    const mine = [{ id: 'everything', tag: 'x', match: [{ cls: 'Generator' as const }] }]
    const m = matchType('Generator', { name: 'g', carrier: 'solar', bus: 'AC bus' }, ctx(), mine)
    expect(m?.type.tag).toBe('x')
  })
})

describe('legendFor', () => {
  it('lists the types present, in table order, with label, colour and icon from the entry', () => {
    const objs = [{ kind: 'wind', carrier: 'onwind' }, { kind: 'bess', carrier: 'battery' }, { kind: 'wind', carrier: 'offwind-ac' }, { kind: 'thermal', carrier: 'coal' }]
    const legend = legendFor(objs)
    expect(legend.map(e => e.id)).toEqual(['wind', 'thermal', 'bess'])
    expect(legend.map(e => e.icon)).toEqual(['renewable', 'thermal', 'battery'])
    for (const e of legend) expect(e.color).toBe(assetTypeOf(e.id)!.color)
    expect(legend.find(e => e.id === 'thermal')!.label).toMatch(/coal/)
  })
})
