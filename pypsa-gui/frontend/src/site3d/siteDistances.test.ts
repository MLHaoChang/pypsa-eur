// S3: site distances — along the map's route when the branch has one, else
// Manhattan × the routing factor; the two-point route the site writes; the
// branches a placement change touches; the flow-path label.
import { describe, expect, it } from 'vitest'
import {
  affectedBranches, connectionAnchor, distanceLabel, distanceTitle, flowAnchorsAlong, edgeKeyOf,
  siteDistances, siteRoutePoints, yardsOf, type DistanceObject, type V3,
} from './siteDistances'
import { ROUTING_FACTOR } from './assetLibrary'
import { fromLocal, toLocal } from './geo'

const ORIGIN = { lng: 6.835, lat: 53.435 }
const flow = (d: number, h: number): { from: V3; to: V3 } => ({ from: [0, -d, h], to: [0, d, h] })

// Two yards 200 m apart along east; a feeder bay in A's yard turned east
// (heading 90: its +north side faces B); a transformer between the yards;
// a link whose far bus is not a member; a generator (no far bus).
const A: DistanceObject = { type: 'Bus', name: 'A', bus: 'A', origin: [0, 0], heading: 0 }
const B: DistanceObject = { type: 'Bus', name: 'Campus 33 kV', bus: 'Campus 33 kV', origin: [200, 0], heading: 0 }
const L1: DistanceObject = { type: 'Line', name: 'L1', bus: 'A', far: 'Campus 33 kV', origin: [30, 0], heading: 90, anchors: { flow: flow(6, 8) } }
const TR: DistanceObject = { type: 'Transformer', name: 'TR1', bus: 'A', far: 'Campus 33 kV', origin: [100, 0], heading: 90, anchors: { flow: flow(3, 5) } }
const K: DistanceObject = { type: 'Link', name: 'K', bus: 'A', far: 'Z', origin: [0, 40], heading: 0, anchors: { flow: flow(2, 3) } }
const G: DistanceObject = { type: 'Generator', name: 'PV', bus: 'A', origin: [-50, -50], heading: 0 }
const objects = [A, B, L1, TR, K, G]

const lngLat = (x: number, y: number): [number, number] => { const p = fromLocal(ORIGIN, { x, y }); return [p.lng, p.lat] }

describe('connectionAnchor', () => {
  it('is the far-side end of the flow anchor, turned by the heading, from the origin', () => {
    expect(connectionAnchor(L1).map(v => +v.toFixed(6))).toEqual([36, 0])   // local (0, +6) faces east at heading 90
    expect(connectionAnchor({ ...L1, heading: 0 }).map(v => +v.toFixed(6))).toEqual([30, 6])
    expect(connectionAnchor({ ...L1, heading: 180 }).map(v => +v.toFixed(6))).toEqual([30, -6])
  })
  it('falls back to the origin without a flow anchor', () => {
    expect(connectionAnchor(A)).toEqual([0, 0])
  })
})

describe('siteDistances', () => {
  it('without a route: Manhattan from the connection anchor to the far yard × the routing factor, per member branch', () => {
    const d = siteDistances(objects, {}, ORIGIN)
    expect([...d.keys()].sort()).toEqual(['line:L1', 'tr:TR1'])
    const l1 = d.get('line:L1')!
    expect(l1.source).toBe('grid')
    expect(l1.metres).toBeCloseTo((200 - 36) * ROUTING_FACTOR, 6)
    expect(l1.far).toBe('Campus 33 kV')
    expect(l1.path).toBeNull()
    const tr = d.get('tr:TR1')!
    expect(tr.metres).toBeCloseTo((200 - 103) * ROUTING_FACTOR, 6)
  })
  it('Manhattan, not Euclidean, when the far yard is off the axis', () => {
    const b = { ...B, origin: [200, 100] as [number, number] }
    const d = siteDistances([A, b, L1], {}, ORIGIN).get('line:L1')!
    expect(d.metres).toBeCloseTo((164 + 100) * ROUTING_FACTOR, 6)
  })
  it('with a route: yard → waypoints → yard on the tangent plane, with the path in site metres', () => {
    const routes = { 'line:L1': { points: [lngLat(100, 50)] } }
    const d = siteDistances(objects, routes, ORIGIN)
    const l1 = d.get('line:L1')!
    expect(l1.source).toBe('route')
    expect(l1.metres).toBeCloseTo(2 * Math.hypot(100, 50), 3)
    expect(l1.path!.map(p => p.map(v => +v.toFixed(3)))).toEqual([[0, 0], [100, 50], [200, 0]])
    // The transformer has no route: still the grid measure.
    expect(d.get('tr:TR1')!.source).toBe('grid')
  })
  it('a route of another kind with the same name is not this branch\'s', () => {
    const d = siteDistances(objects, { 'link:L1': { points: [lngLat(100, 50)] } }, ORIGIN)
    expect(d.get('line:L1')!.source).toBe('grid')
  })
  it('omits a branch whose far bus is not a member, and non-branches', () => {
    const d = siteDistances(objects, {}, ORIGIN)
    expect(d.has('link:K')).toBe(false)
    expect([...d.keys()].some(k => k.includes('PV'))).toBe(false)
  })
  it('follows a moved yard (a placement): the site measures what the user sees', () => {
    const moved = { ...B, origin: [300, 0] as [number, number] }
    expect(siteDistances([A, moved, L1], {}, ORIGIN).get('line:L1')!.metres).toBeCloseTo((300 - 36) * ROUTING_FACTOR, 6)
  })
})

describe('siteRoutePoints', () => {
  it('is the connection anchor and the far yard, as [lat, lng], in the site frame\'s lng/lat', () => {
    const pts = siteRoutePoints(L1, yardsOf(objects), ORIGIN)!
    expect(pts).toHaveLength(2)
    const back = pts.map(([lat, lng]) => { const p = toLocal(ORIGIN, { lng, lat }); return [+p.x.toFixed(6), +p.y.toFixed(6)] })
    expect(back).toEqual([[36, 0], [200, 0]])
  })
  it('is null when the far bus has no yard here', () => {
    expect(siteRoutePoints(K, yardsOf(objects), ORIGIN)).toBeNull()
  })
  it('round-trips through siteDistances as a route whose length is the straight run from the owner yard through the anchor', () => {
    const [a, b] = siteRoutePoints(L1, yardsOf(objects), ORIGIN)!
    const routes = { 'line:L1': { points: [[a[1], a[0]], [b[1], b[0]]] as [number, number][] } }
    const d = siteDistances(objects, routes, ORIGIN).get('line:L1')!
    expect(d.source).toBe('route')
    expect(d.metres).toBeCloseTo(36 + 164, 3)      // A yard → anchor → B yard (the far leg is zero)
  })
})

describe('affectedBranches', () => {
  it('names the branch itself, or a branch at either end of a moved yard; never a non-member branch', () => {
    const keys = (moved: string[]) => affectedBranches(objects, new Set(moved)).map(o => o.name).sort()
    expect(keys(['Transformer:TR1'])).toEqual(['TR1'])
    expect(keys(['Bus:A'])).toEqual(['L1', 'TR1'])
    expect(keys(['Bus:Campus 33 kV'])).toEqual(['L1', 'TR1'])
    expect(keys(['Link:K', 'Generator:PV'])).toEqual([])
    expect(keys([])).toEqual([])
  })
})

describe('labels', () => {
  it('edgeKeyOf keys branches as the map does', () => {
    expect(edgeKeyOf(L1)).toBe('line:L1')
    expect(edgeKeyOf(TR)).toBe('tr:TR1')
    expect(edgeKeyOf(K)).toBe('link:K')
    expect(edgeKeyOf(G)).toBeNull()
  })
  it('reads "TR1 → Campus 33 kV · 86 m", in metres under a kilometre and kilometres above', () => {
    expect(distanceLabel(TR, { metres: 86.4, source: 'grid', far: 'Campus 33 kV', path: null })).toBe('TR1 → Campus 33 kV · 86 m')
    expect(distanceLabel(L1, { metres: 1234, source: 'route', far: 'Campus 33 kV', path: [] })).toBe('L1 → Campus 33 kV · 1.2 km')
  })
  it('the title says how it was measured', () => {
    expect(distanceTitle({ metres: 1, source: 'route', far: 'B', path: [] })).toMatch(/route/i)
    expect(distanceTitle({ metres: 1, source: 'grid', far: 'B', path: null })).toContain(`× ${ROUTING_FACTOR}`)
  })
})

describe('flowAnchorsAlong', () => {
  const l1 = { ...L1, anchors: { flow: flow(6, 8), emissive: true } }
  it('turns a site-metre path into the object\'s frame at the anchor\'s height, keeping the other anchors', () => {
    const a = flowAnchorsAlong(l1, [[0, 0], [100, 50], [200, 0]])
    const r = (v: V3) => v.map(x => +x.toFixed(6) || 0)   // `|| 0` folds −0
    // Heading 90: site east is the object's +north; site north is the object's −east.
    expect(r(a.flow!.from)).toEqual([0, -30, 8])
    expect(a.flow!.via!.map(r)).toEqual([[-50, 70, 8]])
    expect(r(a.flow!.to)).toEqual([0, 170, 8])
    expect(a.emissive).toBe(true)
  })
  it('returns the anchors untouched without a path or without a flow anchor', () => {
    expect(flowAnchorsAlong(l1, null)).toBe(l1.anchors)
    const g = { ...G, anchors: { emissive: true } }
    expect(flowAnchorsAlong(g, [[0, 0], [1, 1]])).toBe(g.anchors)
  })
})
