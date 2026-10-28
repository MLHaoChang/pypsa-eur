// S2 (visual-layers plan 3): the placement check — findings a user can
// read, one per broken rule, never a block.
import { describe, it, expect } from 'vitest'
import {
  placementFindings, findingColor, findingKeys, rectOf, rectDistance, rectsOverlap, betweenSegment, segmentRectDistance,
  bearingDeg, angleDiffDeg, pairRequirement, type CheckObject, type PlacementFinding,
} from './placementCheck'
import { buildSiteLayout, type SiteInput } from './layout'
import { DEFAULT_LIBRARY, PACKING } from './assetLibrary'
import type { Generator, Load, StorageUnit, Store, Transformer, Line } from '../api/types'

const gen = (o: Partial<Generator>): Generator => ({ name: 'g', bus: 'MV', carrier: 'gas', p_nom: 10, ...o } as Generator)
const su  = (o: Partial<StorageUnit>): StorageUnit => ({ name: 's', bus: 'MV', carrier: 'battery', p_nom: 10, max_hours: 4, ...o } as StorageUnit)
const st  = (o: Partial<Store>): Store => ({ name: 'st', bus: 'MV', carrier: 'H2', e_nom: 100, ...o } as Store)
const ld  = (o: Partial<Load>): Load => ({ name: 'l', bus: 'MV', carrier: 'AC', p_set: 20, ...o } as Load)
const tr  = (o: Partial<Transformer>): Transformer => ({ name: 't', bus0: 'HV', bus1: 'MV', s_nom: 100, v_nom_0: 110, v_nom_1: 33, ...o } as Transformer)
const ln  = (o: Partial<Line>): Line => ({ name: 'ln', bus0: 'MV', bus1: 'HV', s_nom: 200, ...o } as Line)

const campus: SiteInput = {
  buses: [{ name: 'HV', v_nom: 110, offset: [0, 0] }, { name: 'MV', v_nom: 33, offset: [400, 0] }],
  transformers: [tr({ name: 'TR1' }), tr({ name: 'TR2' })],
  storageUnits: [su({ name: 'BESS' })],
  generators: [gen({ name: 'GEN', p_nom: 20 }), gen({ name: 'PV', carrier: 'solar', p_nom: 2 })],
  loads: [ld({ name: 'HALL', p_set: 30 })],
  stores: [st({ name: 'H2' })],
  lines: [ln({ name: 'TIE' })],
  links: [],
}

const objectsOf = (input: SiteInput) => buildSiteLayout(input).objects
const obj = (objects: CheckObject[], name: string) => objects.find(o => o.name === name)!
const kinds = (f: PlacementFinding[]) => f.map(x => x.kind)

describe('placementFindings', () => {
  it('reports nothing on the arranged campus', () => {
    expect(placementFindings(objectsOf(campus), DEFAULT_LIBRARY)).toEqual([])
  })

  it('outsideBoundary: the fit check\'s keys become findings, red', () => {
    const f = placementFindings(objectsOf(campus), DEFAULT_LIBRARY, { outside: ['Load:HALL'] })
    expect(f).toEqual([{ key: 'Load:HALL', kind: 'outsideBoundary', severity: 'warn', message: 'HALL sticks out of the site boundary' }])
    expect(findingColor('outsideBoundary')).toBe('red')
  })

  it('overlap: two objects on the same spot, reported once for the pair, naming both', () => {
    const base = objectsOf(campus)
    const bess = obj(base, 'BESS')
    const objects = base.map(o => o.name === 'GEN' ? { ...o, origin: bess.origin, heading: 0 } : o)
    const f = placementFindings(objects, DEFAULT_LIBRARY).filter(x => x.kind === 'overlap' && x.other === 'StorageUnit:BESS')
    expect(f).toHaveLength(1)
    // Reported on the first of the two in site order (GEN is listed before the BESS at its bus).
    expect(f[0]).toMatchObject({ key: 'Generator:GEN', other: 'StorageUnit:BESS', severity: 'warn' })
    expect(f[0].message).toBe('GEN overlaps BESS')
    expect(findingColor('overlap')).toBe('amber')
    expect(findingKeys(f)).toEqual(new Set(['StorageUnit:BESS', 'Generator:GEN']))
  })

  it('keepOut: a genset dropped beside the hall names the hall, the distance and the rule', () => {
    const base = objectsOf(campus)
    const hall = obj(base, 'HALL')
    const at: [number, number] = [hall.origin[0] + hall.footprint[0] / 2 + 10 + obj(base, 'GEN').footprint[0] / 2, hall.origin[1]]
    const objects = base.map(o => o.name === 'GEN' ? { ...o, origin: at, heading: 0 } : o)
    const f = placementFindings(objects, DEFAULT_LIBRARY).filter(x => x.kind === 'keepOut')
    expect(f).toHaveLength(1)
    expect(f[0]).toMatchObject({ key: 'Generator:GEN', other: 'Load:HALL', severity: 'warn', distanceM: 10 })
    expect(f[0].message).toBe('GEN is 10 m from HALL; it should keep 30 m away')
    expect(findingColor('keepOut')).toBe('red')
  })

  it('keepOut is reported on the object whose rule it is, whichever of the two moved', () => {
    const base = objectsOf(campus)
    const gen = obj(base, 'GEN')
    const at: [number, number] = [gen.origin[0], gen.origin[1] - gen.footprint[1] / 2 - 5 - obj(base, 'HALL').footprint[1] / 2]
    const objects = base.map(o => o.name === 'HALL' ? { ...o, origin: at, heading: 0 } : o)
    const all = placementFindings(objects, DEFAULT_LIBRARY).filter(x => x.kind === 'keepOut')
    expect(all.filter(x => x.key === 'Generator:GEN' && x.other === 'Load:HALL')).toHaveLength(1)
    expect(all.filter(x => x.key === 'Load:HALL')).toHaveLength(0)   // never on the hall, which has no rule
  })

  it('clearance: PV too close to the BESS, amber, with the distance it needs', () => {
    const base = objectsOf(campus)
    const bess = obj(base, 'BESS')
    const pv = obj(base, 'PV')
    const at: [number, number] = [bess.origin[0], bess.origin[1] - bess.footprint[1] / 2 - 3 - pv.footprint[1] / 2]
    const objects = base.map(o => o.name === 'PV' ? { ...o, origin: at, heading: 0 } : o)
    const f = placementFindings(objects, DEFAULT_LIBRARY).filter(x => x.kind === 'clearance' && x.other === 'StorageUnit:BESS')
    expect(f).toHaveLength(1)
    expect(f[0]).toMatchObject({ key: 'Generator:PV', severity: 'info', distanceM: 3 })
    expect(f[0].message).toBe('PV is 3 m from BESS; it needs 10 m clear')
    expect(findingColor('clearance')).toBe('amber')
  })

  it('a keep-out breach is not also reported as a clearance breach', () => {
    // H₂ storage: a 50 m keep-out from everything; move it to 5 m from the PV field, which has a 10 m clearance.
    const base = objectsOf(campus)
    const pv = obj(base, 'PV'), h2 = obj(base, 'H2')
    const at: [number, number] = [pv.origin[0], pv.origin[1] + pv.footprint[1] / 2 + 5 + h2.footprint[1] / 2]
    const objects = base.map(o => o.name === 'H2' ? { ...o, origin: at, heading: 0 } : o)
    const f = placementFindings(objects, DEFAULT_LIBRARY).filter(x => x.other === 'Generator:PV' || x.key === 'Generator:PV')
    expect(f.filter(x => x.kind === 'keepOut')).toHaveLength(1)
    expect(f.filter(x => x.kind === 'clearance')).toHaveLength(0)
  })

  it('notBetweenBuses: a transformer away from the segment between its yards says how far', () => {
    const base = objectsOf(campus)
    // Still turned towards the MV yard from up there, so only the position is wrong.
    const objects = base.map(o => o.name === 'TR1' ? { ...o, origin: [200, 150] as [number, number], heading: bearingDeg([200, 150], obj(base, 'MV').origin) } : o)
    const f = placementFindings(objects, DEFAULT_LIBRARY).filter(x => x.key === 'Transformer:TR1')
    expect(kinds(f)).toEqual(['notBetweenBuses'])
    expect(f[0].severity).toBe('info')
    expect(f[0].message).toMatch(/^TR1 is 1\d\d m from the line between the HV and MV yards; it should sit between its two buses$/)
    expect(findingColor('notBetweenBuses')).toBe('amber')
  })

  it('notBetweenBuses is not raised when the far bus is not a member (there is no segment)', () => {
    const one = buildSiteLayout({ ...campus, buses: [campus.buses[1]] }).objects
    const objects = one.map(o => o.name === 'TR1' ? { ...o, origin: [900, 900] as [number, number] } : o)
    expect(placementFindings(objects, DEFAULT_LIBRARY).filter(x => x.key === 'Transformer:TR1' && x.kind === 'notBetweenBuses')).toEqual([])
  })

  it('notFacingFar: a transformer on its segment but turned north, when its far bus is east', () => {
    const base = objectsOf(campus)
    const objects = base.map(o => o.name === 'TR1' ? { ...o, heading: 0 } : o)
    const f = placementFindings(objects, DEFAULT_LIBRARY).filter(x => x.key === 'Transformer:TR1')
    expect(kinds(f)).toEqual(['notFacingFar'])
    expect(f[0]).toMatchObject({ severity: 'info', other: 'Bus:MV' })
    expect(f[0].message).toBe('TR1 faces 90° away from the MV yard; it should face it')
    // Within tolerance is fine.
    const nearly = base.map(o => o.name === 'TR1' ? { ...o, heading: o.heading + PACKING.facingToleranceDeg - 1 } : o)
    expect(placementFindings(nearly, DEFAULT_LIBRARY).filter(x => x.kind === 'notFacingFar')).toEqual([])
  })

  it('a feeder whose far bus is a member is checked for facing; one whose far bus is elsewhere is not', () => {
    const base = objectsOf(campus)
    const turned = base.map(o => o.name === 'TIE' ? { ...o, heading: o.heading + 90 } : o)
    expect(placementFindings(turned, DEFAULT_LIBRARY).filter(x => x.key === 'Line:TIE').map(x => x.kind)).toEqual(['notFacingFar'])
    const ext = objectsOf({ ...campus, lines: [ln({ name: 'TIE', bus0: 'MV', bus1: 'EXT' })] })
    expect(placementFindings(ext.map(o => o.name === 'TIE' ? { ...o, heading: 123 } : o), DEFAULT_LIBRARY).filter(x => x.key === 'Line:TIE')).toEqual([])
  })

  it('rooftop objects are exempt (they stand on their hall by design)', () => {
    const objects = objectsOf({ ...campus, generators: [...campus.generators, gen({ name: 'ROOF', carrier: 'solar-rooftop', p_nom: 1 })] })
    expect(obj(objects, 'ROOF').elevation).toBeGreaterThan(0)
    expect(placementFindings(objects, DEFAULT_LIBRARY)).toEqual([])
  })

  it('findings are in object order, then by kind, and deterministic', () => {
    const base = objectsOf(campus)
    const objects = base.map(o => o.name === 'TR1' ? { ...o, origin: [200, 150] as [number, number], heading: 0 } : o)
    const a = placementFindings(objects, DEFAULT_LIBRARY), b = placementFindings(objects, DEFAULT_LIBRARY)
    expect(a).toEqual(b)
    expect(kinds(a.filter(x => x.key === 'Transformer:TR1'))).toEqual(['notBetweenBuses', 'notFacingFar'])
  })
})

describe('geometry', () => {
  it('rectOf turns the footprint by the heading (clockwise from north)', () => {
    const r = rectOf({ origin: [10, 20], footprint: [6, 2], heading: 90 })
    expect(r.x1 - r.x0).toBeCloseTo(2, 6)
    expect(r.y1 - r.y0).toBeCloseTo(6, 6)
    expect([(r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2]).toEqual([10, 20])
  })
  it('rectDistance is 0 for touching or overlapping rectangles, the gap otherwise', () => {
    expect(rectDistance({ x0: 0, x1: 1, y0: 0, y1: 1 }, { x0: 1, x1: 2, y0: 0, y1: 1 })).toBe(0)
    expect(rectDistance({ x0: 0, x1: 1, y0: 0, y1: 1 }, { x0: 4, x1: 5, y0: 4, y1: 5 })).toBeCloseTo(Math.hypot(3, 3), 6)
    expect(rectsOverlap({ x0: 0, x1: 1, y0: 0, y1: 1 }, { x0: 1, x1: 2, y0: 0, y1: 1 })).toBe(false)
    expect(rectsOverlap({ x0: 0, x1: 2, y0: 0, y1: 2 }, { x0: 1, x1: 3, y0: 1, y1: 3 })).toBe(true)
  })
  it('betweenSegment runs edge to edge along the centre line, null when the yards touch', () => {
    const seg = betweenSegment({ x0: -10, x1: 10, y0: -5, y1: 5 }, { x0: 90, x1: 110, y0: -5, y1: 5 })!
    expect(seg.a).toEqual([10, 0])
    expect(seg.b).toEqual([90, 0])
    expect(seg.dir).toEqual([1, 0])
    expect(betweenSegment({ x0: 0, x1: 10, y0: 0, y1: 10 }, { x0: 5, x1: 15, y0: 0, y1: 10 })).toBeNull()
    expect(betweenSegment({ x0: 0, x1: 10, y0: 0, y1: 10 }, { x0: 0, x1: 10, y0: 0, y1: 10 })).toBeNull()
  })
  it('segmentRectDistance is 0 when the segment crosses the rectangle', () => {
    expect(segmentRectDistance([0, 0], [100, 0], { x0: 40, x1: 60, y0: -30, y1: 30 })).toBe(0)
    expect(segmentRectDistance([0, 0], [100, 0], { x0: 40, x1: 60, y0: 10, y1: 30 })).toBe(10)
    expect(segmentRectDistance([0, 0], [100, 0], { x0: 110, x1: 120, y0: -5, y1: 5 })).toBe(10)
  })
  it('bearings are clockwise from north; angle differences wrap', () => {
    expect(bearingDeg([0, 0], [0, 1])).toBe(0)
    expect(bearingDeg([0, 0], [1, 0])).toBe(90)
    expect(bearingDeg([0, 0], [0, -1])).toBe(180)
    expect(bearingDeg([0, 0], [-1, 0])).toBe(270)
    expect(angleDiffDeg(350, 10)).toBe(20)
    expect(angleDiffDeg(90, 90)).toBe(0)
    expect(angleDiffDeg(0, 180)).toBe(180)
  })
  it('pairRequirement takes the larger of both rules, keep-out by type or "*"', () => {
    const genset = { keepOutM: [{ from: ['load'], m: 30 }] }
    const pv = { clearanceM: 10 }
    const h2 = { keepOutM: [{ from: ['*'], m: 50 }] }
    expect(pairRequirement('thermal', genset, 'load', undefined)).toEqual({ keepOutM: 30, clearanceM: 0 })
    expect(pairRequirement('load', undefined, 'thermal', genset)).toEqual({ keepOutM: 30, clearanceM: 0 })
    expect(pairRequirement('thermal', genset, 'pv', pv)).toEqual({ keepOutM: 0, clearanceM: 10 })
    expect(pairRequirement('h2store', h2, 'pv', pv)).toEqual({ keepOutM: 50, clearanceM: 10 })
  })
})

// SC-02 (V17–V19): native scene buildings as obstacles and roofs; exact versus
// approximate sizes; findings name the objects and an unknown rule is not a pass.
import {
  nativeFindings, polygonContains, shapeOverlapsPolygon, shapeOnRoof, roofEligible, sizeBasis, noSizeCopy, buildingKey, type NativeObstacle,
} from './placementCheck'

const square = (cx: number, cy: number, half: number): [number, number][] =>
  [[cx - half, cy - half], [cx + half, cy - half], [cx + half, cy + half], [cx - half, cy + half]]
const building = (id: string, label: string, cx: number, cy: number, half: number, height = 9): NativeObstacle =>
  ({ id, label, footprint: square(cx, cy, half), height_m: height })

describe('native buildings (V17)', () => {
  it('polygon geometry: containment, overlap and a roof that holds the whole footprint', () => {
    const ring = square(0, 0, 10)
    expect(polygonContains(ring, [0, 0])).toBe(true)
    expect(polygonContains(ring, [11, 0])).toBe(false)
    expect(shapeOverlapsPolygon({ origin: [9, 0], footprint: [4, 4], heading: 0 }, ring)).toBe(true)
    expect(shapeOverlapsPolygon({ origin: [20, 0], footprint: [4, 4], heading: 0 }, ring)).toBe(false)
    // Touching an edge is not an overlap.
    expect(shapeOverlapsPolygon({ origin: [12, 0], footprint: [4, 4], heading: 0 }, ring)).toBe(false)
    expect(shapeOnRoof({ origin: [0, 0], footprint: [6, 6], heading: 45 }, ring)).toBe(true)
    expect(shapeOnRoof({ origin: [8, 0], footprint: [6, 6], heading: 0 }, ring)).toBe(false)
    expect(roofEligible({ footprint: ring, height_m: 9 })).toBe(true)
    expect(roofEligible({ footprint: ring, height_m: 0 })).toBe(false)
    expect(roofEligible({ footprint: ring.slice(0, 2), height_m: 9 })).toBe(false)
  })

  it('a native building is an obstacle: the packer keeps the BESS beside it, never on it', () => {
    const base = objectsOf(campus)
    const bess = obj(base, 'BESS')
    const hall = building('osm:7', 'OSM building 7', bess.origin[0], bess.origin[1], 15)
    const layout = buildSiteLayout({ ...campus, buildings: [hall] })
    const moved = obj(layout.objects, 'BESS')
    expect(shapeOverlapsPolygon(moved, hall.footprint)).toBe(false)
    expect(rectDistance(rectOf(moved), { x0: hall.footprint[0][0], x1: hall.footprint[2][0], y0: hall.footprint[0][1], y1: hall.footprint[2][1] }))
      .toBeLessThan(60)
    const packed = layout.objects.filter(o => o.type !== 'Bus')
    expect(packed.filter(o => shapeOverlapsPolygon(o, hall.footprint))).toEqual([])
    expect(nativeFindings(layout.objects, DEFAULT_LIBRARY, { buildings: [hall] }).filter(f => f.kind === 'overlapsBuilding')).toEqual([])
  })

  it('a BESS placed into a building is a finding that names both, and selects both', () => {
    const base = objectsOf(campus)
    const bess = obj(base, 'BESS')
    const hall = building('osm:7', 'OSM building 7', bess.origin[0], bess.origin[1], 15)
    const layout = buildSiteLayout({ ...campus, buildings: [hall], placements: { 'StorageUnit:BESS': { x: bess.origin[0], y: bess.origin[1], heading: 0 } } })
    const f = nativeFindings(layout.objects, DEFAULT_LIBRARY, { buildings: [hall] }).filter(x => x.kind === 'overlapsBuilding')
    expect(f).toEqual([{ key: 'StorageUnit:BESS', other: buildingKey('osm:7'), kind: 'overlapsBuilding', severity: 'warn', message: 'BESS: overlaps OSM building 7' }])
    // A click selects both: the item and the building.
    expect(new Set(f.flatMap(x => [x.key, x.other]))).toEqual(new Set(['StorageUnit:BESS', 'building:osm:7']))
  })

  it('PV on an eligible roof stands on it, lifted to its height, with no finding', () => {
    const roof = building('osm:9', 'OSM building 9', 300, 300, 30, 12)
    const layout = buildSiteLayout({
      ...campus, buildings: [roof],
      footprints: { 'Generator:PV': { width_m: 10, depth_m: 10 } },
      placements: { 'Generator:PV': { x: 300, y: 300, heading: 0, on: 'roof:osm:9' } },
    })
    const pv = obj(layout.objects, 'PV')
    expect(pv).toMatchObject({ origin: [300, 300], roof: 'osm:9', elevation: 12 })
    expect(layout.placed).toContain('Generator:PV')
    expect(nativeFindings(layout.objects, DEFAULT_LIBRARY, { buildings: [roof] }).filter(f => f.key === 'Generator:PV')).toEqual([])
  })

  it('off the roof and not for a roof: each a finding in words', () => {
    const roof = building('osm:9', 'OSM building 9', 0, 0, 10)
    const pv: CheckObject = { type: 'Generator', name: 'PV', kind: 'rooftopPv', bus: 'MV', origin: [9, 0], footprint: [6, 6], heading: 0, elevation: 9, roof: 'osm:9' }
    const roofKind = DEFAULT_LIBRARY.find(t => t.zone === 'roof')!.id
    const off = nativeFindings([{ ...pv, kind: roofKind }], DEFAULT_LIBRARY, { buildings: [roof] })
    expect(off).toEqual([{ key: 'Generator:PV', other: 'building:osm:9', kind: 'offRoof', severity: 'warn', message: 'PV: off the roof of OSM building 9' }])
    const bess: CheckObject = { ...pv, type: 'StorageUnit', name: 'BESS', kind: 'bess', origin: [0, 0] }
    expect(nativeFindings([bess], DEFAULT_LIBRARY, { buildings: [roof] })).toEqual([
      { key: 'StorageUnit:BESS', kind: 'notRoofEligible', severity: 'warn', message: 'Can’t go on a roof: BESS' },
    ])
  })

  it('a transformer stays in the yard: a building elsewhere changes nothing about it', () => {
    const without = objectsOf(campus)
    const far = building('osm:1', 'OSM building 1', -900, -900, 20)
    const withB = buildSiteLayout({ ...campus, buildings: [far] }).objects
    expect(obj(withB, 'TR1').origin).toEqual(obj(without, 'TR1').origin)
    expect(nativeFindings(withB, DEFAULT_LIBRARY, { buildings: [far] })).toEqual([])
    expect(placementFindings(withB, DEFAULT_LIBRARY)).toEqual([])
  })
})

describe('exact versus approximate size (V18)', () => {
  it('catalogue is exact, no source is entered, a library fit is approximate, none is none', () => {
    expect(sizeBasis({ source: 'catalogue' })).toBe('exact')
    expect(sizeBasis({})).toBe('entered')
    expect(sizeBasis({ source: 'library' })).toBe('approximate')
    expect(sizeBasis(undefined)).toBe('none')
    expect(sizeBasis(null)).toBe('none')
  })

  it('no size is one line naming the item, never "Approximate size" too', () => {
    expect(noSizeCopy('roof_pv')).toBe('No rule checked: no size entered for roof_pv, so overlap with buildings uses the centre point only')
  })

  it('a native finding keeps the item name as written: "site_battery: overlaps …"', () => {
    const hall = building('osm:1', 'Building 1', 0, 0, 10)
    const o: CheckObject = { type: 'StorageUnit', name: 'site_battery', kind: 'bess', bus: 'MV', origin: [0, 0], footprint: [4, 4], heading: 0 }
    expect(nativeFindings([o], DEFAULT_LIBRARY, { buildings: [hall] }).map(f => f.message)).toEqual(['site_battery: overlaps Building 1'])
  })
})

describe('findings name objects; an unknown rule is not a pass (V19)', () => {
  it('a type without a placement rule says which rule was not checked', () => {
    const base = objectsOf(campus)
    const f = nativeFindings(base, DEFAULT_LIBRARY, { reportUnchecked: true }).filter(x => x.kind === 'noRule')
    expect(f.length).toBeGreaterThan(0)
    for (const x of f) {
      expect(x.message).toMatch(/^No rule checked: spacing for /)
      expect(x.message).not.toMatch(/[A-Za-z]+:[A-Za-z]/)
    }
    // A library with no rules at all never reads as a clean pass.
    const bare = DEFAULT_LIBRARY.map(t => ({ ...t, placement: undefined }))
    expect(nativeFindings(base, bare, { reportUnchecked: true })).not.toEqual([])
  })
})

describe('native findings in the scene overlay', () => {
  it('scene data → layout → findings: the item is outlined, the building named', async () => {
    const { nativeFindingKeys } = await import('./placementCheck')
    const base = objectsOf(campus)
    const bess = obj(base, 'BESS')
    const hall = building('osm:7', 'OSM building 7', bess.origin[0], bess.origin[1], 15)
    const layout = buildSiteLayout({ ...campus, buildings: [hall], placements: { 'StorageUnit:BESS': { x: bess.origin[0], y: bess.origin[1], heading: 0 } } })
    const f = nativeFindings(layout.objects, DEFAULT_LIBRARY, { buildings: [hall] })
    expect(nativeFindingKeys(f, 'amber')).toEqual(new Set(['StorageUnit:BESS']))
    expect(nativeFindingKeys(f, 'red')).toEqual(new Set())
  })
})
