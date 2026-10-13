// Visual-layers plan 1, A2: in *individual* mode every Generator, Load,
// StorageUnit, Store and conversion Link is a node of its own, typed by the
// shared taxonomy; a branch Link stays an edge. The fixture is a small campus
// across four carriers so one of every class, a multi-port Link and an
// excluded feeder are all present.
import { describe, it, expect } from 'vitest'
import type { Generator, Link, Load, StorageUnit, Store } from '../api/types'
import type { MatchContext } from '../utils/assetTypes'
import { formatSizing } from '../utils/assetTypes'
import {
  INDIVIDUAL_MAX_COMPONENTS, RING_RADIUS, assetEdgeId, assetLegend, assetNodeId, assetNodeLinkNames, assetSatellites,
  buildAssetEdges, buildAssetNodes, componentFromAssetNodeId, countNonBusComponents, defaultAssetMode,
  isAssetNodeId, liveAssetPositions, ringOffsets, zoneOf, type AssetNodeComponents,
} from './topologyAssets'

const BUS_CARRIER: Record<string, string> = { B_el: 'AC', B_el2: 'AC', B_h2: 'H2', B_heat: 'heat', B_gas: 'gas' }
const CTX: MatchContext = { members: Object.keys(BUS_CARRIER), busCarrier: b => BUS_CARRIER[b] }

const gen = (name: string, carrier: string, p_nom: number, bus = 'B_el') => ({ name, bus, carrier, p_nom }) as unknown as Generator
const load = (name: string, carrier: string, p_set: number, bus = 'B_el') => ({ name, bus, carrier, p_set }) as unknown as Load
const su = (name: string, carrier: string, p_nom: number, max_hours: number, bus = 'B_el') => ({ name, bus, carrier, p_nom, max_hours }) as unknown as StorageUnit
const store = (name: string, carrier: string, e_nom: number, bus: string) => ({ name, bus, carrier, e_nom }) as unknown as Store
const link = (name: string, carrier: string, bus0: string, bus1: string, p_nom: number, extra: Record<string, unknown> = {}) =>
  ({ name, carrier, bus0, bus1, p_nom, efficiency: 0.7, ...extra }) as unknown as Link

const NET: AssetNodeComponents = {
  generators: [gen('PV', 'solar', 12), gen('Gas', 'gas', 20)],
  loads: [load('DataHall', 'AC', 8), load('H2 offtake', 'H2', 3, 'B_h2')],
  storageUnits: [su('BESS', 'battery', 10, 4), su('Flywheel', 'flywheel', 2, 0.25)],
  stores: [store('H2 tank', 'H2', 500, 'B_h2')],
  links: [
    link('Electrolyser', 'H2 electrolysis', 'B_el', 'B_h2', 5),
    link('CHP', 'gas', 'B_gas', 'B_el', 9, { bus2: 'B_heat', efficiency2: 0.4 }),
    link('HVDC', 'DC', 'B_el', 'B_el2', 100),             // a branch between two electrical buses: an edge, not a node
    link('Orphan', 'H2 electrolysis', 'Nowhere', 'B_h2', 1), // owner port not in the network: skipped
  ],
}

const nodes = buildAssetNodes(NET, CTX)
const byName = (name: string) => nodes.find(n => n.name === name)!

describe('buildAssetNodes over the fixture', () => {
  it('draws one node per non-branch component, and none for the feeder or the orphan', () => {
    expect(nodes).toHaveLength(9)
    expect(nodes.map(n => n.name)).toEqual(['PV', 'Gas', 'DataHall', 'H2 offtake', 'BESS', 'Flywheel', 'H2 tank', 'Electrolyser', 'CHP'])
  })

  it('ids carry the class and the name, and round-trip', () => {
    expect(byName('PV').id).toBe('asset-Generator:PV')
    expect(assetNodeId('Load', 'a:b')).toBe('asset-Load:a:b')
    expect(componentFromAssetNodeId('asset-Load:a:b')).toEqual({ cls: 'Load', name: 'a:b' })
    expect(componentFromAssetNodeId('B_el')).toBeNull()
    expect(componentFromAssetNodeId('assetgrp-B_el-Load')).toBeNull()
    expect(componentFromAssetNodeId('asset-Bus:x')).toBeNull()
    expect(isAssetNodeId('asset-Store:S')).toBe(true)
  })

  it('types, labels, colours and icons come from the shared taxonomy', () => {
    expect(byName('PV')).toMatchObject({ typeId: 'pv', typeLabel: 'PV field', color: '#16a34a', icon: 'sun' })
    expect(byName('Gas')).toMatchObject({ typeId: 'thermal', typeLabel: 'Engine gensets (gas)' })
    expect(byName('DataHall')).toMatchObject({ typeId: 'load', typeLabel: 'Data hall', icon: 'load_elec' })
    expect(byName('H2 offtake')).toMatchObject({ typeId: 'offtake' })
    expect(byName('BESS')).toMatchObject({ typeId: 'bess', icon: 'battery' })
    expect(byName('H2 tank')).toMatchObject({ typeId: 'h2store', icon: 'hydrogen' })
    expect(byName('Electrolyser')).toMatchObject({ typeId: 'electrolyser', icon: 'electrolyzer' })
    expect(byName('CHP')).toMatchObject({ typeId: 'chp', icon: 'chp' })
  })

  it('the sizing figure is the one the type names', () => {
    const fig = (name: string) => formatSizing(byName(name).sizing!)
    expect(fig('PV')).toBe('12 MW')
    expect(fig('DataHall')).toBe('8 MW')
    expect(fig('BESS')).toBe('40 MWh')      // p_nom × max_hours
    expect(fig('Flywheel')).toBe('2 MW')    // the library sizes flywheels by power
    expect(fig('H2 tank')).toBe('500 MWh')  // e_nom
    expect(fig('Electrolyser')).toBe('5 MW')
    expect(byName('BESS').maxHours).toBe(4)
  })

  it('a Link stands at the port its type names, and lists every bus it connects to', () => {
    expect(byName('Electrolyser').bus).toBe('B_el')
    expect(byName('Electrolyser').buses).toEqual([{ bus: 'B_el', port: 'bus0' }, { bus: 'B_h2', port: 'bus1' }])
    expect(byName('CHP').bus).toBe('B_el')
    expect(byName('CHP').buses).toEqual([{ bus: 'B_el', port: 'bus1' }, { bus: 'B_gas', port: 'bus0' }, { bus: 'B_heat', port: 'bus2' }])
    expect([...assetNodeLinkNames(nodes)]).toEqual(['Electrolyser', 'CHP'])
  })

  it('an asset edge per bus; a multi-port Link suffixes the port', () => {
    const edges = buildAssetEdges(nodes)
    expect(edges).toHaveLength(7 + 2 + 3)
    expect(edges.find(e => e.target === 'asset-Generator:PV')).toMatchObject({ id: 'assetedge-asset-Generator:PV', source: 'B_el', color: '#16a34a' })
    expect(edges.filter(e => e.target === 'asset-Link:CHP').map(e => e.id)).toEqual([
      assetEdgeId('asset-Link:CHP', 'bus1'), assetEdgeId('asset-Link:CHP', 'bus0'), assetEdgeId('asset-Link:CHP', 'bus2'),
    ])
    for (const e of edges) expect(e.id.startsWith('assetedge-')).toBe(true)
  })

  it('legend rows: one per type present, in table order, with the shared colour and icon', () => {
    const rows = assetLegend(nodes)
    expect(rows.map(r => r.id)).toEqual(['pv', 'thermal', 'flywheel', 'h2store', 'bess', 'electrolyser', 'chp', 'offtake', 'load'])
    expect(rows.find(r => r.id === 'thermal')?.label).toBe('Engine gensets (gas)')
    expect(rows.find(r => r.id === 'bess')).toMatchObject({ color: '#7c3aed', icon: 'battery' })
  })
})

describe('placement around the bus', () => {
  it('uses the 3D packer zone words: generation north, loads south, storage east, conversion west', () => {
    expect(zoneOf(byName('PV'))).toBe('north')
    expect(zoneOf(byName('Gas'))).toBe('north')
    expect(zoneOf(byName('DataHall'))).toBe('south')
    expect(zoneOf(byName('BESS'))).toBe('east')
    expect(zoneOf(byName('H2 tank'))).toBe('east')
    expect(zoneOf(byName('Electrolyser'))).toBe('west')
    expect(zoneOf({ typeId: 'unknown', cls: 'Load' })).toBe('south')
  })

  it('offsets point into the zone, sit on the first ring, and are deterministic', () => {
    const a = ringOffsets(nodes), b = ringOffsets(nodes)
    expect([...a.entries()]).toEqual([...b.entries()])
    const off = (name: string) => a.get(byName(name).id)!
    expect(off('PV').dy).toBeLessThan(0)          // north is up (screen y grows downward)
    expect(off('DataHall').dy).toBeGreaterThan(0)
    expect(off('BESS').dx).toBeGreaterThan(0)
    expect(off('Electrolyser').dx).toBeLessThan(0)
    for (const n of nodes) {
      const o = a.get(n.id)!
      expect(Math.hypot(o.dx, o.dy)).toBeGreaterThan(RING_RADIUS - 12)
      expect(Math.hypot(o.dx, o.dy)).toBeLessThan(RING_RADIUS + 12)
    }
  })

  it('two nodes of one zone at one bus get distinct slots', () => {
    const a = ringOffsets(nodes)
    const pv = a.get(byName('PV').id)!, gas = a.get(byName('Gas').id)!
    expect(Math.hypot(pv.dx - gas.dx, pv.dy - gas.dy)).toBeGreaterThan(100)
  })

  it('a fifth node in a zone moves out to the next ring', () => {
    const many = buildAssetNodes({ ...NET, generators: ['a', 'b', 'c', 'd', 'e'].map(n => gen(n, 'solar', 1)) }, CTX)
    const offs = ringOffsets(many)
    const radii = ['a', 'b', 'c', 'd', 'e'].map(n => { const o = offs.get(assetNodeId('Generator', n))!; return Math.hypot(o.dx, o.dy) })
    expect(radii.slice(0, 4).every(r => Math.abs(r - RING_RADIUS) < 12)).toBe(true)
    expect(radii[4]).toBeGreaterThan(RING_RADIUS + 80)
  })

  it('satellites name the anchor bus', () => {
    const sats = assetSatellites(nodes)
    expect(sats.find(s => s.id === 'asset-Link:CHP')).toMatchObject({ busId: 'B_el' })
    expect(sats.find(s => s.id === 'asset-Store:H2 tank')).toMatchObject({ busId: 'B_h2' })
  })
})

describe('default mode', () => {
  it('is individual up to the threshold and grouped above it', () => {
    expect(INDIVIDUAL_MAX_COMPONENTS).toBe(40)
    expect(defaultAssetMode(0)).toBe('individual')
    expect(defaultAssetMode(INDIVIDUAL_MAX_COMPONENTS)).toBe('individual')
    expect(defaultAssetMode(INDIVIDUAL_MAX_COMPONENTS + 1)).toBe('grouped')
  })
  it('counts every non-bus component', () => {
    expect(countNonBusComponents({ generators: 1, loads: 2, storageUnits: 3, stores: 4, links: 5, lines: 6, transformers: 7 })).toBe(28)
  })
})

describe('prune on save', () => {
  it('keeps cached asset positions whose component exists, drops the rest, never touches buses', () => {
    const cache = {
      'B_el': { x: 0, y: 0 },
      'asset-Generator:PV': { x: 10, y: 20 },
      'asset-Generator:Gone': { x: 30, y: 40 },
      'assetgrp-B_el-Load': { x: 1, y: 1 },
    }
    const live = new Set(nodes.map(n => n.id))
    expect(liveAssetPositions(cache, live)).toEqual([{ id: 'asset-Generator:PV', canvasX: 10, canvasY: 20 }])
  })
})
