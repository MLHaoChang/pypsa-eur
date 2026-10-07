// WP1 review-gate blocker: a part's geometry is built once, with the
// cylinder's axis baked in (r3f re-ran an onUpdate rotation at mount and
// undid it). SiteCanvas-only (three); no WebGL needed for geometry.
import { describe, it, expect } from 'vitest'
import { Box3, Vector3 } from 'three'
import { partShape, partMatrix } from './partGeometry'
import { runTemplate } from './templates'
import { DEFAULT_LIBRARY } from './assetLibrary'
import { toScene } from './scene'
import type { Part } from './templates'

const size = (p: Part) => new Box3().setFromBufferAttribute(partShape(p).getAttribute('position') as never).getSize(new Vector3())

describe('partShape', () => {
  it('a box is (east, height, north) in scene axes', () => {
    expect(size({ pos: [0, 0, 0], size: [6, 2, 3] }).toArray()).toEqual([6, 3, 2])
  })
  it('a north-lying cylinder [d, L, d] lies along scene Z', () => {
    const s = size({ pos: [0, 0, 0], size: [3, 20, 3], shape: 'cylinder', axis: 'north' })
    expect(s.x).toBeCloseTo(3, 5); expect(s.y).toBeCloseTo(3, 5); expect(s.z).toBeCloseTo(20, 5)
  })
  it('an east-lying cylinder [L, d, d] lies along scene X', () => {
    const s = size({ pos: [0, 0, 0], size: [20, 3, 3], shape: 'cylinder', axis: 'east' })
    expect(s.x).toBeCloseTo(20, 5); expect(s.y).toBeCloseTo(3, 5); expect(s.z).toBeCloseTo(3, 5)
  })
  it('an upright cylinder [d, d, h] stands along scene Y', () => {
    const s = size({ pos: [0, 0, 0], size: [4, 4, 100], shape: 'cylinder', axis: 'up' })
    expect(s.x).toBeCloseTo(4, 5); expect(s.y).toBeCloseTo(100, 5); expect(s.z).toBeCloseTo(4, 5)
  })
  it('is indexed with position, normal and uv (mergeable)', () => {
    for (const p of [{ pos: [0, 0, 0], size: [1, 1, 1] }, { pos: [0, 0, 0], size: [1, 1, 1], shape: 'cylinder' }] as Part[]) {
      const g = partShape(p)
      expect(g.getIndex()).not.toBeNull()
      expect(Object.keys(g.attributes).sort()).toEqual(['normal', 'position', 'uv'])
    }
  })
})

describe('partMatrix', () => {
  it('places a part at its centre in scene coordinates', () => {
    const v = new Vector3().applyMatrix4(partMatrix({ pos: [10, 20, 3], size: [1, 1, 1] }))
    expect(v.toArray()).toEqual(toScene(10, 20, 3))
  })
  it('turbine blades are radial: each blade\'s inner end is at its hub', () => {
    const wind = DEFAULT_LIBRARY.find(t => t.id === 'wind')!
    const out = runTemplate(wind.geometry, { amount: 15 })
    for (const r of out.anchors.rotors!) {
      const hub = new Vector3(...toScene(...r.hub))
      for (const blade of out.parts.filter(p => p.anchor === 'rotor' && p.turbine === r.turbine)) {
        const inner = new Vector3(-blade.size[0] / 2, 0, 0).applyMatrix4(partMatrix(blade))
        expect(inner.distanceTo(hub), `turbine ${r.turbine}`).toBeLessThan(0.01)
      }
    }
  })
})
