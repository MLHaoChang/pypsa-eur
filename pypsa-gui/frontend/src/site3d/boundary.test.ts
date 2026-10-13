import { describe, it, expect } from 'vitest'
import {
  polygonAreaM2, centroid, pointInBoundary, busesInside, defaultBoundaryFor,
  isValidBoundary, dedupeTrailing, boundaryBounds,
} from './boundary'
import { fromLocal, metresPerDegree } from './geo'
import type { LngLatTuple } from './types'

const ORIGIN = { lng: 6.83, lat: 53.44 }
// A 100 m (east) × 200 m (north) rectangle around ORIGIN, clockwise from NW.
function rect(w = 100, d = 200): LngLatTuple[] {
  return ([[-w / 2, d / 2], [w / 2, d / 2], [w / 2, -d / 2], [-w / 2, -d / 2]] as const)
    .map(([x, y]) => { const ll = fromLocal(ORIGIN, { x, y }); return [ll.lng, ll.lat] as LngLatTuple })
}

describe('polygonAreaM2', () => {
  it('a 100 m × 200 m rectangle at 53°N is 20 000 m² within 1 %', () => {
    expect(polygonAreaM2(rect())).toBeCloseTo(20_000, -2)
    expect(Math.abs(polygonAreaM2(rect()) - 20_000) / 20_000).toBeLessThan(0.01)
  })
  it('is orientation independent and zero for fewer than three vertices', () => {
    expect(polygonAreaM2(rect().slice().reverse())).toBeCloseTo(polygonAreaM2(rect()), 3)
    expect(polygonAreaM2(rect().slice(0, 2))).toBe(0)
  })
})

describe('centroid', () => {
  it('is the centre of a rectangle', () => {
    const c = centroid(rect())
    expect(c.lng).toBeCloseTo(ORIGIN.lng, 8)
    expect(c.lat).toBeCloseTo(ORIGIN.lat, 8)
  })
  it('is area-weighted, not the vertex mean, for an L-shape', () => {
    const m = metresPerDegree(ORIGIN.lat)
    const pt = (x: number, y: number): LngLatTuple => [ORIGIN.lng + x / m.lng, ORIGIN.lat + y / m.lat]
    // L: a 100×100 square plus a 100×100 square to its east-south.
    const L: LngLatTuple[] = [pt(0, 100), pt(100, 100), pt(100, 0), pt(200, 0), pt(200, -100), pt(0, -100)]
    const c = centroid(L)
    const meanLng = L.reduce((a, v) => a + v[0], 0) / L.length
    expect(c.lng).not.toBeCloseTo(meanLng, 7)
    expect(polygonAreaM2(L)).toBeCloseTo(30_000, -2)
  })
})

describe('containment', () => {
  it('inside, outside, and a vertex counts as inside', () => {
    const b = rect()
    expect(pointInBoundary(ORIGIN, b)).toBe(true)
    expect(pointInBoundary(fromLocal(ORIGIN, { x: 500, y: 0 }), b)).toBe(false)
    expect(pointInBoundary({ lng: b[0][0], lat: b[0][1] }, b)).toBe(true)
  })
  it('busesInside ignores unplaced buses and keeps order', () => {
    const inside = fromLocal(ORIGIN, { x: 10, y: 10 })
    const outside = fromLocal(ORIGIN, { x: 1000, y: 10 })
    const buses = [
      { name: 'unplaced', x: 0, y: 0 },
      { name: 'in-1', x: inside.lng, y: inside.lat },
      { name: 'out', x: outside.lng, y: outside.lat },
      { name: 'in-2', x: ORIGIN.lng, y: ORIGIN.lat },
    ]
    expect(busesInside(buses, rect())).toEqual(['in-1', 'in-2'])
  })
})

describe('defaultBoundaryFor', () => {
  it('is a clockwise 4-vertex rectangle 20 % larger than the bounds, centred on the bounds (not the origin)', () => {
    const bounds = { x0: 100, x1: 300, y0: -50, y1: 50 } // packed to the east of the bus
    const b = defaultBoundaryFor(bounds, ORIGIN)
    expect(b).toHaveLength(4)
    expect(isValidBoundary(b)).toBe(true)
    const c = centroid(b)
    const expected = fromLocal(ORIGIN, { x: 200, y: 0 })
    expect(c.lng).toBeCloseTo(expected.lng, 7)
    expect(c.lat).toBeCloseTo(expected.lat, 7)
    const bb = boundaryBounds(b, ORIGIN)
    expect(bb.x1 - bb.x0).toBeCloseTo(240, 0)
    expect(bb.y1 - bb.y0).toBeCloseTo(120, 0)
    // clockwise: NW, NE, SE, SW
    expect(b[0][1]).toBeGreaterThan(b[3][1])
    expect(b[1][0]).toBeGreaterThan(b[0][0])
  })
})

describe('isValidBoundary / dedupeTrailing', () => {
  it('rejects too few vertices, repeated consecutive vertices, and an out-of-range vertex', () => {
    expect(isValidBoundary(rect().slice(0, 2))).toBe(false)
    const dup = [...rect(), rect()[3]]
    expect(isValidBoundary(dup)).toBe(false)
    expect(isValidBoundary([[0, 0], [1, 0], [200, 1]])).toBe(false)
    expect(isValidBoundary(rect())).toBe(true)
  })
  it('dedupeTrailing removes the duplicates a double-click close leaves', () => {
    const r = rect()
    const closed: LngLatTuple[] = [...r.slice(0, 3), r[3], r[3]]
    expect(dedupeTrailing(closed)).toEqual(r)
    expect(dedupeTrailing(r)).toEqual(r)
    expect(dedupeTrailing([[1, 1]])).toEqual([[1, 1]])
  })
})

describe('collapseDuplicateVertices', () => {
  it('collapses consecutive duplicates anywhere, drops a closing duplicate of the first, and tolerates sub-centimetre drift', async () => {
    const { collapseDuplicateVertices } = await import('./boundary')
    const r = rect()
    const jitter: LngLatTuple = [r[1][0] + 2e-8, r[1][1] - 2e-8]
    expect(collapseDuplicateVertices([r[0], r[0], r[0], r[1], jitter, r[2], r[3], r[0]])).toEqual([r[0], r[1], r[2], r[3]])
    expect(collapseDuplicateVertices([])).toEqual([])
  })
})
