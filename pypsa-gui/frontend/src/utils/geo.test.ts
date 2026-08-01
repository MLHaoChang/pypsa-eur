// The contract for "does this bus have a real location?". Every case here maps
// to a success criterion in
// docs/superpowers/specs/2026-07-30-unplaced-buses-map-design.md.
// The Greenwich cases are the point of the file: the rule is the exact PAIR of
// zeroes, and a naive `x === 0 || y === 0` would hide a legitimate bus.
import { describe, expect, it } from 'vitest'
import { busLatLng, isPlaced, unplacedBusNames } from './geo'

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
