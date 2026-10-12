import { describe, it, expect } from 'vitest'
import { fitReport, footprintCorners, type FitObject } from './fit'
import { fromLocal } from './geo'
import type { LngLatTuple, Site } from './types'

const ORIGIN = { lng: 6.83, lat: 53.44 }
// 200 m × 200 m plot centred on the origin.
const boundary: LngLatTuple[] = ([[-100, 100], [100, 100], [100, -100], [-100, -100]] as const)
  .map(([x, y]) => { const ll = fromLocal(ORIGIN, { x, y }); return [ll.lng, ll.lat] as LngLatTuple })
const site: Site = { id: 's', name: 'S', buses: [], boundary, origin: ORIGIN, placements: {} }

const obj = (key: string, origin: [number, number], footprint: [number, number], heading = 0, areaM2?: number): FitObject =>
  ({ key, origin, footprint, heading, areaM2: areaM2 ?? footprint[0] * footprint[1] })

describe('fitReport', () => {
  it('fits: land under plot, nothing outside', () => {
    const r = fitReport([obj('a', [0, 0], [20, 10]), obj('b', [50, 50], [10, 10])], site)
    expect(r.plotM2).toBeCloseTo(40_000, -2)
    expect(r.landM2).toBe(300)
    expect(r.over).toBe(false)
    expect(r.outside).toEqual([])
  })

  it('over: land take exceeds the plot even though every object is inside', () => {
    const r = fitReport([obj('pv', [0, 0], [10, 10], 0, 60_000)], site)
    expect(r.over).toBe(true)
    expect(r.outside).toEqual([])
  })

  it('straddling: an object crossing the fence is reported', () => {
    const r = fitReport([obj('edge', [95, 0], [20, 10])], site)
    expect(r.outside).toEqual(['edge'])
  })

  it('a rotated object whose unrotated box fits can poke a corner out', () => {
    // 60 × 4 bar centred at (75, 0): unrotated it spans x 45..105 → outside.
    // Rotated 90° (pointing north) it spans x 73..77 → inside.
    expect(fitReport([obj('bar', [75, 0], [60, 4], 0)], site).outside).toEqual(['bar'])
    expect(fitReport([obj('bar', [75, 0], [60, 4], 90)], site).outside).toEqual([])
    // And the reverse: a north-south bar at (0, 75) reaches y = 105 at 0° and
    // lies flat (y 73..77) at 90°; an east-west bar there turns north-south at 90°.
    expect(fitReport([obj('bar2', [0, 75], [4, 60], 0)], site).outside).toEqual(['bar2'])
    expect(fitReport([obj('bar2', [0, 75], [4, 60], 90)], site).outside).toEqual([])
    expect(fitReport([obj('bar3', [0, 75], [60, 4], 90)], site).outside).toEqual(['bar3'])
  })
})

describe('footprintCorners', () => {
  it('heading is clockwise from north: 90° turns the east extent to point south', () => {
    const c = footprintCorners({ origin: [0, 0], footprint: [10, 2], heading: 90 })
    // The east-most corner of the unrotated box (5, 1) moves to (1, -5) under a clockwise quarter turn.
    const xs = c.map(p => Math.round(p.x)), ys = c.map(p => Math.round(p.y))
    expect(Math.max(...xs)).toBe(1)
    expect(Math.min(...ys)).toBe(-5)
  })
})
