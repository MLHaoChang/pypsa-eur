// The contract for "does this bus have a real location?". Every case here maps
// to a success criterion in
// docs/superpowers/specs/2026-07-30-unplaced-buses-map-design.md.
// The Greenwich cases are the point of the file: the rule is the exact PAIR of
// zeroes, and a naive `x === 0 || y === 0` would hide a legitimate bus.
import { describe, expect, it } from 'vitest'
import {
  LENGTH_DISCREPANCY_ABS_KM, LENGTH_DISCREPANCY_REL, busLatLng, fmtKm, haversineKm, isPlaced,
  lengthDisagrees, routeLengthKm, unplacedBusNames,
} from './geo'

describe('busLatLng', () => {
  it("treats PyPSA's (0, 0) default as not placed", () => {
    expect(busLatLng({ x: 0, y: 0 })).toBeNull()
    expect(busLatLng({ x: -0, y: 0 })).toBeNull()
    expect(busLatLng({ x: 0, y: -0 })).toBeNull()
    // netCDF omits all-default columns, so an untouched network arrives with
    // no x/y at all rather than with zeroes.
    expect(busLatLng({ x: null, y: null })).toBeNull()
    expect(busLatLng({ x: undefined, y: undefined })).toBeNull()
  })

  it('keeps a bus on either zero meridian — only the exact pair means unset', () => {
    expect(busLatLng({ x: 0, y: 51.5 })).toEqual([51.5, 0])     // Greenwich
    expect(busLatLng({ x: 6.96, y: 0 })).toEqual([0, 6.96])     // equator
  })

  it('swaps PyPSA (x=lng, y=lat) into Leaflet [lat, lng]', () => {
    expect(busLatLng({ x: 6.96, y: 50.938 })).toEqual([50.938, 6.96])
  })

  it('rejects non-finite and out-of-range values', () => {
    expect(busLatLng({ x: NaN, y: 50 })).toBeNull()
    expect(busLatLng({ x: 6.96, y: 91 })).toBeNull()
    expect(busLatLng({ x: 6.96, y: -91 })).toBeNull()
    expect(busLatLng({ x: 181, y: 50 })).toBeNull()
    expect(busLatLng({ x: -181, y: 50 })).toBeNull()
  })

  it('rejects a MIXED null/set pair rather than coercing the null half to 0', () => {
    // `Number(null) === 0`, so a naive `Number(b.x)` coercion turns a bus with
    // only ONE coordinate missing into a fabricated point on the meridian or
    // equator instead of "not placed". This is reachable: NaN coordinates
    // serialise to JSON `null` (services/serialization.py `clean_scalar`), so
    // a bus that is finite on one axis and NaN on the other arrives here as
    // exactly this shape.
    expect(busLatLng({ x: null, y: 51.5 })).toBeNull()
    expect(busLatLng({ x: 6.96, y: null })).toBeNull()
    expect(busLatLng({ x: undefined, y: 51.5 })).toBeNull()
    expect(busLatLng({ x: 6.96, y: undefined })).toBeNull()
  })
})

describe('isPlaced', () => {
  it('is the boolean face of busLatLng', () => {
    expect(isPlaced({ x: 0, y: 0 })).toBe(false)
    expect(isPlaced({ x: 6.96, y: 50.938 })).toBe(true)
  })
})

describe('unplacedBusNames', () => {
  it('names every bus still at the default, in input order', () => {
    expect(unplacedBusNames([
      { name: 'B1', x: 0, y: 0 },
      { name: 'B2', x: 6.96, y: 50.9 },
      { name: 'B3', x: 0, y: 0 },
    ])).toEqual(['B1', 'B3'])
  })

  it('is empty when every bus is placed', () => {
    expect(unplacedBusNames([{ name: 'B1', x: 6.96, y: 50.9 }])).toEqual([])
  })

  it('is empty for an empty network', () => {
    expect(unplacedBusNames([])).toEqual([])
  })
})

// ── Lengths from geometry (plan M2) ─────────────────────────────────────────
// The same geodesics the backend's `route_length_km` is tested against
// (tests/test_route_length.py), so the badge computed here agrees with the
// length the server writes.

const ONE_DEGREE_KM = (Math.PI * 6371) / 180   // ≈ 111.19

describe('haversineKm / routeLengthKm', () => {
  it('one degree of latitude is about 111.2 km', () => {
    expect(haversineKm([0, 0], [1, 0])).toBeCloseTo(ONE_DEGREE_KM, 9)
    expect(routeLengthKm([[53, 6], [54, 6]])).toBeGreaterThan(111.1)
    expect(routeLengthKm([[53, 6], [54, 6]])).toBeLessThan(111.3)
  })

  it('a two-leg route is the sum of its legs', () => {
    const a: [number, number] = [53.4396, 6.8321], b: [number, number] = [53.4401, 6.834], c: [number, number] = [53.445, 6.84]
    expect(routeLengthKm([a, b, c])).toBeCloseTo(haversineKm(a, b) + haversineKm(b, c), 12)
  })

  it('a bend is longer than the chord', () => {
    expect(routeLengthKm([[53, 6], [53.5, 6.5], [53, 7]])).toBeGreaterThan(routeLengthKm([[53, 6], [53, 7]]))
  })

  it('fewer than two points is zero', () => {
    expect(routeLengthKm([])).toBe(0)
    expect(routeLengthKm([[53, 6]])).toBe(0)
    expect(routeLengthKm([[53, 6], [53, 6]])).toBe(0)
  })

  it('takes [lat, lng] — Cologne to Berlin is ~475 km only in that order', () => {
    const cologne: [number, number] = [50.938, 6.96], berlin: [number, number] = [52.52, 13.405]
    const km = routeLengthKm([cologne, berlin])
    expect(km).toBeGreaterThan(460)
    expect(km).toBeLessThan(490)
    const swapped = routeLengthKm([[6.96, 50.938], [13.405, 52.52]])
    expect(swapped > 460 && swapped < 490).toBe(false)
  })
})

describe('lengthDisagrees — the discrepancy badge threshold', () => {
  it('is 10 % or 100 m, whichever is larger', () => {
    expect(LENGTH_DISCREPANCY_REL).toBe(0.1)
    expect(LENGTH_DISCREPANCY_ABS_KM).toBe(0.1)
    // Long line: 10 % rules. 3.1 km geometry tolerates ±310 m.
    expect(lengthDisagrees(2.4, 3.1)).toBe(true)
    expect(lengthDisagrees(2.9, 3.1)).toBe(false)
    expect(lengthDisagrees(3.4, 3.1)).toBe(false)
    expect(lengthDisagrees(3.42, 3.1)).toBe(true)
    // Short feeder: 100 m rules. 50 m geometry tolerates ±100 m, not ±5 m.
    expect(lengthDisagrees(0.14, 0.05)).toBe(false)
    expect(lengthDisagrees(0.16, 0.05)).toBe(true)
    // A typed 1 km default on a 300 m campus run is flagged; the reverse too.
    expect(lengthDisagrees(1.0, 0.3)).toBe(true)
    expect(lengthDisagrees(0.3, 1.0)).toBe(true)
  })

  it('exact agreement and the boundary itself do not flag', () => {
    expect(lengthDisagrees(3.1, 3.1)).toBe(false)
    expect(lengthDisagrees(3.41, 3.1)).toBe(false)
  })

  it('never flags when a value is missing or not finite', () => {
    expect(lengthDisagrees(null, 3.1)).toBe(false)
    expect(lengthDisagrees(undefined, 3.1)).toBe(false)
    expect(lengthDisagrees(NaN, 3.1)).toBe(false)
    expect(lengthDisagrees(3.1, NaN)).toBe(false)
  })
})

describe('fmtKm', () => {
  it('reads as a human would say it', () => {
    expect(fmtKm(0.34)).toBe('340 m')
    expect(fmtKm(2.4)).toBe('2.4 km')
    expect(fmtKm(475.3)).toBe('475 km')
    expect(fmtKm(1250)).toBe('1,250 km')
    expect(fmtKm(NaN)).toBe('—')
  })
})
