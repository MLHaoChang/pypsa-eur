// Phase 2 plan Task 2.1: one merged, vertex-coloured geometry per object;
// each turbine's rotor separate, in hub-local coordinates.
import { describe, it, expect } from 'vitest'
import { Box3, Color, Vector3 } from 'three'
import { objectGeometry } from './objectGeometry'
import { partShape, partMatrix } from './partGeometry'
import { runTemplate, type Part } from './templates'
import { DEFAULT_LIBRARY } from './assetLibrary'
import { toScene } from './scene'

const run = (id: string, amount: number) => runTemplate(DEFAULT_LIBRARY.find(t => t.id === id)!.geometry, { amount })

describe('objectGeometry', () => {
  it('merges every non-rotor part: vertex count = Σ each part\'s own shape', () => {
    const out = run('bess', 40)
    const g = objectGeometry(out.parts, '#7c3aed')
    const expected = out.parts.reduce((a, p) => a + partShape(p).getAttribute('position').count, 0)
    expect(g.body!.getAttribute('position').count).toBe(expected)
    expect(g.body!.getIndex()).not.toBeNull()
    expect(g.rotors).toHaveLength(0)
  })

  it('a box is 24 vertices and a 16-segment cylinder 100', () => {
    expect(objectGeometry([{ pos: [0, 0, 0], size: [1, 1, 1] }], '#000').body!.getAttribute('position').count).toBe(24)
    expect(objectGeometry([{ pos: [0, 0, 0], size: [1, 1, 1], shape: 'cylinder' }], '#000').body!.getAttribute('position').count).toBe(100)
  })

  it('colours each part\'s vertices with its own colour, else the object\'s', () => {
    const parts: Part[] = [{ pos: [0, 0, 0], size: [1, 1, 1] }, { pos: [5, 0, 0], size: [1, 1, 1], color: '#ff0000' }]
    const col = objectGeometry(parts, '#0000ff').body!.getAttribute('color')
    const first = new Color(col.getX(0), col.getY(0), col.getZ(0)), last = new Color(col.getX(47), col.getY(47), col.getZ(47))
    expect(first.getHexString()).toBe(new Color('#0000ff').getHexString())
    expect(last.getHexString()).toBe(new Color('#ff0000').getHexString())
  })

  it('places each part as partMatrix does (position, rotations, cylinder axis)', () => {
    const parts: Part[] = [
      { pos: [10, 20, 3], size: [2, 4, 6] },
      { pos: [0, 0, 1.5], size: [3, 20, 3], shape: 'cylinder', axis: 'north' },
      { pos: [0, 0, 1.2], size: [30, 2.2, 0.1], rotX: 25 * Math.PI / 180 },
      { pos: [3, -4, 2], size: [8, 1, 2], rotZ: 0.7 },
      { pos: [0, -8, 100], size: [70, 0.5, 3], rotN: -2 * Math.PI / 3 },
      { pos: [1, 1, 3], size: [20, 3, 3], shape: 'cylinder', axis: 'east' },
      { pos: [1, 1, 50], size: [4, 4, 100], shape: 'cylinder', axis: 'up' },
      { pos: [5, 5, 5], size: [3, 2, 1], rotX: 0.3, rotZ: -0.4, rotN: 1.1 },
    ]
    for (const p of parts) {
      const merged = new Box3().setFromBufferAttribute(objectGeometry([p], '#000').body!.getAttribute('position') as never)
      const direct = new Box3().setFromBufferAttribute(partShape(p).applyMatrix4(partMatrix(p)).getAttribute('position') as never)
      expect(merged.min.distanceTo(direct.min)).toBeLessThan(1e-6)
      expect(merged.max.distanceTo(direct.max)).toBeLessThan(1e-6)
    }
  })

  it('returns one rotor per turbine, in hub-local coordinates, with the hub as its origin', () => {
    const out = run('wind', 15)
    const g = objectGeometry(out.parts, '#15803d', out.anchors)
    expect(g.rotors.map(r => r.turbine)).toEqual([0, 1, 2])
    for (const r of g.rotors) {
      expect(r.origin).toEqual(toScene(...out.anchors.rotors!.find(x => x.turbine === r.turbine)!.hub))
      // Hub-local: the three blades' inner ends meet at the local origin.
      const bb = new Box3().setFromBufferAttribute(r.geometry.getAttribute('position') as never)
      expect(bb.getCenter(new Vector3()).length()).toBeLessThan(40)
      expect(bb.containsPoint(new Vector3(0, 0, 0))).toBe(true)
    }
    // The body holds towers and nacelles only.
    const bodyVerts = g.body!.getAttribute('position').count
    const nonRotor = out.parts.filter(p => p.anchor !== 'rotor').reduce((a, p) => a + partShape(p).getAttribute('position').count, 0)
    expect(bodyVerts).toBe(nonRotor)
  })

  it('a hero turbine drops the parametric tower, nacelle and rotors (they are all heroable)', () => {
    const out = run('wind', 15)
    const g = objectGeometry(out.parts, '#000', out.anchors, p => !p.heroable)
    expect(g.body).toBeNull()
    expect(g.rotors).toHaveLength(0)
  })

  it('omits the parts a caller leaves out (heroes, WP3), and an empty body is null', () => {
    const out = run('bess', 40)
    const g = objectGeometry(out.parts, '#000', out.anchors, p => !p.heroable)
    expect(g.body!.getAttribute('position').count).toBe(2 * 24)   // the two PCS skids
    expect(objectGeometry([], '#000').body).toBeNull()
  })

  it('merges 120 parts quickly (median of ten runs after a warm-up, so parallel load does not decide it)', () => {
    const parts: Part[] = Array.from({ length: 120 }, (_, i) => ({ pos: [i * 3, 0, 1], size: [2, 2, 2], shape: i % 2 ? 'cylinder' : 'box' }))
    objectGeometry(parts, '#000')
    const times = Array.from({ length: 10 }, () => { const t0 = performance.now(); objectGeometry(parts, '#000'); return performance.now() - t0 }).sort((a, b) => a - b)
    expect(times[5]).toBeLessThan(50)
  })
})
