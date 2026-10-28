import { describe, expect, it } from 'vitest'
import { boundaryAround, boundaryErrors, BOUNDARY_DEFAULTS, greenfieldFromBuilding, osmSourcesInside, rectangleLocal } from './nativeSite'
import { toLocal } from './geo'
import type { SceneBuilding } from '../api/siteScene'
import type { ContextBuilding, Site } from './types'

const anchor = { lng: 11, lat: 48 }

describe('native site helpers', () => {
  it('the default boundary is a 200 m square centred on the anchor', () => {
    const ring = boundaryAround(anchor, BOUNDARY_DEFAULTS).map(([lng, lat]) => toLocal(anchor, { lng, lat }))
    expect(ring[1].x - ring[0].x).toBeCloseTo(200, 6)
    expect(ring[3].y - ring[0].y).toBeCloseTo(200, 6)
    expect(ring.reduce((s, p) => s + p.x, 0)).toBeCloseTo(0, 6)
  })

  it('bounds the sides and the offset with visible reasons', () => {
    expect(boundaryErrors(BOUNDARY_DEFAULTS)).toEqual({})
    const e = boundaryErrors({ ...BOUNDARY_DEFAULTS, width: 5, east: 1200, heading: NaN })
    expect(e.width).toBe('Enter 10–5,000 m')
    expect(e.east).toBe('Enter at most 1,000 m either way')
    expect(e.heading).toBeDefined()
  })

  it('reads a greenfield rectangle back to an equivalent one', () => {
    const ring = rectangleLocal({ x: 10, y: -5 }, 30, 12, 25)
    const b: SceneBuilding = { id: 'greenfield:a', source: { namespace: 'greenfield', source_id: 'a' },
      footprint: ring.map(p => [p.x, p.y]), height_m: 9, height_evidence: 'greenfield', levels: null }
    const g = greenfieldFromBuilding(b)!
    const back = rectangleLocal({ x: g.center[0], y: g.center[1] }, g.width, g.depth, g.heading)
    for (let i = 0; i < 4; i++) {
      const hit = back.some(q => Math.hypot(q.x - ring[i].x, q.y - ring[i].y) < 0.05)
      expect(hit).toBe(true)
    }
    expect(g.height).toBe(9)
  })

  it('keeps OSM buildings inside the boundary, with tagged heights and levels only', () => {
    const site: Site = { id: 's', name: 'S', buses: [], origin: anchor, placements: {}, boundary: boundaryAround(anchor, BOUNDARY_DEFAULTS) }
    const sq = (dx: number): ContextBuilding['polygon'] => [[11 + dx, 48], [11 + dx + 0.0001, 48], [11 + dx + 0.0001, 48.0001], [11 + dx, 48.0001]]
    const blds: ContextBuilding[] = [
      { id: 1, polygon: sq(0), height_m: 12, height_source: 'tag', tags: {} },
      { id: 2, polygon: sq(0.0002), height_m: 9, height_source: 'levels', tags: { 'building:levels': '3' } },
      { id: 3, polygon: sq(-0.0003), height_m: 8, height_source: 'default', tags: {} },
      { id: 4, polygon: sq(0.05), height_m: 8, height_source: 'tag', tags: {} },
    ]
    const out = osmSourcesInside(site, blds)
    expect(out.map(s => s.source_id)).toEqual(['1', '2', '3'])
    expect(out[0].height_m).toBe(12)
    expect(out[1]).toMatchObject({ levels: 3 })
    expect(out[1].height_m).toBeUndefined()
    expect(out[2].height_m).toBeUndefined()
  })
})
