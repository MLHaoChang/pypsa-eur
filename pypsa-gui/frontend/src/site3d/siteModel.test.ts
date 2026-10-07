import { describe, it, expect } from 'vitest'
import { newSite, newSiteId, siteForBus, primaryBus, siteBounds, busOffsets, defaultSiteName } from './siteModel'
import { fromLocal } from './geo'
import { SITE_ID_RE, type LngLatTuple, type SitesDocument } from './types'

const ORIGIN = { lng: 6.83, lat: 53.44 }
const rect: LngLatTuple[] = ([[-50, 100], [50, 100], [50, -100], [-50, -100]] as const)
  .map(([x, y]) => { const ll = fromLocal(ORIGIN, { x, y }); return [ll.lng, ll.lat] as LngLatTuple })

describe('newSite', () => {
  it('sets the origin to the centroid and a safe 22-char id', () => {
    const s = newSite({ name: 'Campus', boundary: rect, buses: ['B1'] })
    expect(s.origin.lng).toBeCloseTo(ORIGIN.lng, 8)
    expect(s.origin.lat).toBeCloseTo(ORIGIN.lat, 8)
    expect(s.id).toMatch(SITE_ID_RE)
    expect(s.id).toHaveLength(22)
    expect(s.placements).toEqual({})
    expect(s.boundary).not.toBe(rect) // copied
  })
  it('newSiteId is deterministic for a given random source', () => {
    const fixed = (n: number) => new Uint8Array(n).fill(255)
    expect(newSiteId(fixed)).toBe('_'.repeat(21) + 'w')
    expect(newSiteId(fixed)).toMatch(SITE_ID_RE)
  })
})

describe('membership and frames', () => {
  const site = newSite({ name: 'Campus', boundary: rect, buses: ['B1', 'B2', 'ghost'], id: 'site_a' })
  const doc: SitesDocument = { version: 1, sites: [site] }

  it('siteForBus / primaryBus', () => {
    expect(siteForBus(doc, 'B2')?.id).toBe('site_a')
    expect(siteForBus(doc, 'nope')).toBeNull()
    expect(primaryBus(site)).toBe('B1')
    expect(primaryBus({ ...site, buses: [] })).toBeNull()
  })

  it('siteBounds is the boundary in metres from the origin', () => {
    const b = siteBounds(site)
    expect(b.x0).toBeCloseTo(-50, 0); expect(b.x1).toBeCloseTo(50, 0)
    expect(b.y0).toBeCloseTo(-100, 0); expect(b.y1).toBeCloseTo(100, 0)
  })

  it('busOffsets gives each placed member bus its metres from the origin and omits the rest', () => {
    const b1 = fromLocal(ORIGIN, { x: 30, y: -20 })
    const buses = [
      { name: 'B1', x: b1.lng, y: b1.lat },
      { name: 'B2', x: 0, y: 0 },          // unplaced
      { name: 'other', x: ORIGIN.lng, y: ORIGIN.lat }, // not a member
    ]
    const off = busOffsets(site, buses)
    expect(Object.keys(off)).toEqual(['B1'])
    expect(off.B1[0]).toBeCloseTo(30, 3)
    expect(off.B1[1]).toBeCloseTo(-20, 3)
  })

  it('defaultSiteName counts up', () => {
    expect(defaultSiteName({ version: 1, sites: [] })).toBe('Site 1')
    expect(defaultSiteName(doc)).toBe('Site 2')
  })
})
