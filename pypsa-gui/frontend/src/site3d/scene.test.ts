import { describe, it, expect } from 'vitest'
import { toScene, toBoxArgs, fitCamera, chooseSite, unionBounds, halfSizeFor } from './scene'
import type { Site, SitesDocument } from './types'


describe('frame mapping', () => {
  it('north is −Z and up is +Y', () => {
    expect(toScene(1, 2, 3)).toEqual([1, 3, -2])
    expect(toBoxArgs([6.1, 2.44, 2.9])).toEqual([6.1, 2.9, 2.44])
  })
})

describe('fitCamera', () => {
  const bounds = { x0: -300, x1: 300, y0: -700, y1: 100 }

  it('sits due south of the bounds centre, above it, looking at the centre', () => {
    const c = fitCamera(bounds, 1.5)
    expect(c.target).toEqual(toScene(0, -300, 0))
    expect(c.position[0]).toBe(0)            // same easting as the centre
    expect(c.position[1]).toBeGreaterThan(0) // above
    expect(c.position[2]).toBeGreaterThan(300) // south of the centre (+Z is south)
    expect(c.far).toBeGreaterThan(5000)
  })

  it('backs off further for a narrow (portrait) viewport when width is the binding extent', () => {
    const wideSite = { x0: -800, x1: 800, y0: -100, y1: 100 }
    const wide = fitCamera(wideSite, 2)
    const narrow = fitCamera(wideSite, 0.75)
    expect(narrow.position[1]).toBeGreaterThan(wide.position[1])
  })

  it('every corner of the bounds is inside the horizontal field of view', () => {
    for (const aspect of [0.6, 1, 2.4]) {
      const c = fitCamera(bounds, aspect)
      const [px, py, pz] = c.position
      const halfFov = (45 * Math.PI) / 360
      for (const [ex, ny] of [[bounds.x0, bounds.y0], [bounds.x1, bounds.y0], [bounds.x0, bounds.y1], [bounds.x1, bounds.y1]] as const) {
        const [sx, sy, sz] = toScene(ex, ny, 0)
        const dx = sx - px, dy = sy - py, dz = sz - pz
        // Horizontal angle off the view axis (the axis lies in the y–z plane, x = 0).
        const forward = Math.hypot(dy, dz)
        expect(Math.abs(Math.atan2(dx, forward))).toBeLessThan(Math.atan(Math.tan(halfFov) * aspect))
      }
    }
  })
})

describe('chooseSite', () => {
  const mk = (id: string, buses: string[]): Site => ({ id, name: id, buses, boundary: [[0, 0], [0.01, 0], [0.01, 0.01]], origin: { lng: 0.005, lat: 0.003 }, placements: {} })
  const doc: SitesDocument = { version: 1, sites: [mk('a', ['B1']), mk('b', ['B2', 'B3'])] }

  it('prefers the active id, then the selected bus\'s site, then the first', () => {
    expect(chooseSite(doc, 'b', 'B1')?.id).toBe('b')
    expect(chooseSite(doc, 'missing', 'B3')?.id).toBe('b')
    expect(chooseSite(doc, null, 'nope')?.id).toBe('a')
    expect(chooseSite(doc, null, null)?.id).toBe('a')
  })
  it('is null with no sites', () => {
    expect(chooseSite({ version: 1, sites: [] }, 'a', 'B1')).toBeNull()
  })
})

describe('unionBounds / halfSizeFor', () => {
  it('union contains both and halfSize rounds up in 50 m steps with a 250 m floor', () => {
    const u = unionBounds({ x0: -10, x1: 20, y0: -5, y1: 5 }, { x0: 0, x1: 400, y0: -300, y1: 1 })
    expect(u).toEqual({ x0: -10, x1: 400, y0: -300, y1: 5 })
    expect(halfSizeFor({ x0: -1, x1: 1, y0: -1, y1: 1 })).toBe(300)
    expect(halfSizeFor(u)).toBe(500)
  })
})
