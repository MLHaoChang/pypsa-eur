import { describe, it, expect } from 'vitest'
import {
  metresPerDegree, toLocal, fromLocal, lngLatToTile, tileToLngLat,
  tileRangeAround, tileCount, mosaicExtent, zoomFor,
} from './geo'

const GREENWICH = { lng: 0, lat: 51.4779 }

describe('tangent plane', () => {
  it('one degree of latitude is ~111 km everywhere; longitude shrinks with cos(lat)', () => {
    const eq = metresPerDegree(0)
    expect(eq.lat).toBeCloseTo(111319.5, 0)
    expect(eq.lng).toBeCloseTo(111319.5, 0)
    const g = metresPerDegree(51.4779)
    expect(g.lat).toBeCloseTo(111319.5, 0)
    expect(g.lng).toBeCloseTo(69326, -2)
  })

  it('the origin projects to (0, 0) and east/north are positive', () => {
    expect(toLocal(GREENWICH, GREENWICH)).toEqual({ x: 0, y: 0 })
    const p = toLocal(GREENWICH, { lng: 0.01, lat: 51.4879 })
    expect(p.x).toBeGreaterThan(0)
    expect(p.y).toBeGreaterThan(0)
    expect(p.y).toBeCloseTo(1113.2, 0)
  })

  it('fromLocal inverts toLocal', () => {
    const back = fromLocal(GREENWICH, toLocal(GREENWICH, { lng: 0.004, lat: 51.48 }))
    expect(back.lng).toBeCloseTo(0.004, 9)
    expect(back.lat).toBeCloseTo(51.48, 9)
  })
})

describe('web mercator tiles', () => {
  it('the world is one tile at z0 and (0,0) is its centre', () => {
    const t = lngLatToTile({ lng: 0, lat: 0 }, 0)
    expect(t.x).toBeCloseTo(0.5)
    expect(t.y).toBeCloseTo(0.5)
  })

  it('tileToLngLat inverts lngLatToTile at integer corners', () => {
    const c = tileToLngLat(65, 43, 7)
    const t = lngLatToTile(c, 7)
    expect(t.x).toBeCloseTo(65, 6)
    expect(t.y).toBeCloseTo(43, 6)
  })

  it('a ~1 km square at z17 near 51°N is a small mosaic that contains the square', () => {
    const r = tileRangeAround(GREENWICH, 500, 17)
    expect(r.z).toBe(17)
    expect(tileCount(r)).toBeGreaterThanOrEqual(9)
    expect(tileCount(r)).toBeLessThanOrEqual(36)
    const ext = mosaicExtent(GREENWICH, r)
    expect(ext.west).toBeLessThanOrEqual(-500)
    expect(ext.east).toBeGreaterThanOrEqual(500)
    expect(ext.south).toBeLessThanOrEqual(-500)
    expect(ext.north).toBeGreaterThanOrEqual(500)
  })

  it('clamps the range at the tile grid edge instead of asking for a tile that does not exist', () => {
    const r = tileRangeAround({ lng: -179.999, lat: 0 }, 2000, 5)
    expect(r.x0).toBe(0)
  })

  it('zoomFor picks a zoom where the square is about the target tile count across', () => {
    // ~1 km at 51°N: one z17 tile is ~187 m, so 1 km ≈ 5.3 tiles → z17.
    expect(zoomFor(51.4779, 500)).toBe(17)
    // Larger sites zoom out; never past the imagery's ceiling.
    expect(zoomFor(51.4779, 5000)).toBeLessThan(17)
    expect(zoomFor(51.4779, 10)).toBe(19)
  })
})
