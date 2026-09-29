import { describe, it, expect } from 'vitest'
import { toScene, toBoxArgs, chooseSiteBus, fitCamera } from './scene'
import type { Bus } from '../api/types'

const bus = (name: string, x: number, y: number): Bus => ({ name, x, y } as Bus)

describe('frame mapping', () => {
  it('north is −Z and up is +Y', () => {
    expect(toScene(1, 2, 3)).toEqual([1, 3, -2])
    expect(toBoxArgs([6.1, 2.44, 2.9])).toEqual([6.1, 2.9, 2.44])
  })
})

describe('chooseSiteBus', () => {
  const buses = [bus('unplaced', 0, 0), bus('A', 4.9, 52.4), bus('B', 4.95, 52.41)]

  it('is null when no bus is placed', () => {
    expect(chooseSiteBus([bus('unplaced', 0, 0)], { type: 'Bus', name: 'unplaced' }, 'unplaced')).toBeNull()
  })

  it('prefers the selected bus, then the previous one, then the first placed', () => {
    expect(chooseSiteBus(buses, { type: 'Bus', name: 'B' }, 'A')).toBe('B')
    expect(chooseSiteBus(buses, { type: 'Generator', name: 'g' }, 'B')).toBe('B')
    expect(chooseSiteBus(buses, null, null)).toBe('A')
  })

  it('never picks an unplaced bus even when it is selected or was previous', () => {
    expect(chooseSiteBus(buses, { type: 'Bus', name: 'unplaced' }, 'unplaced')).toBe('A')
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
