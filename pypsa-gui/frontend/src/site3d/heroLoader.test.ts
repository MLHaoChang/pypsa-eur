// Phase 2 plan Tasks 3.3–3.4, checked through the real matrices (three runs
// in jsdom; nothing is mounted or rendered): where each hero's box lands in
// the object's frame, against the parametric unit it replaces — computed
// here from the model's bounds, not by the code under test — the rotor
// pivot and size, the mirror removal, and the per-object tint.
import { describe, it, expect } from 'vitest'
import * as THREE from 'three'
import { HERO_MODELS, heroInstances, type HeroModel } from './heroes'
import { heroPieces, instanceMatrix, rotorMatrix, tintMaterial, TINT_MAP_FRAGMENT } from './heroLoader'
import { runTemplate, type Part } from './templates'
import { DEFAULT_LIBRARY } from './assetLibrary'

const entry = (id: string) => DEFAULT_LIBRARY.find(t => t.id === id)!
type Box = { min: [number, number, number]; max: [number, number, number] }

/** The model's bounds pushed through a matrix, as a box in (east, north, up). Scene (x, y, z) = (east, up, −north). */
function worldBox(m: HeroModel, mat: THREE.Matrix4): Box {
  const min: [number, number, number] = [Infinity, Infinity, Infinity], max: [number, number, number] = [-Infinity, -Infinity, -Infinity]
  for (const x of [m.bounds.min[0], m.bounds.max[0]]) for (const y of [m.bounds.min[1], m.bounds.max[1]]) for (const z of [m.bounds.min[2], m.bounds.max[2]]) {
    const v = new THREE.Vector3(x, y, z).applyMatrix4(mat)
    const enu = [v.x, -v.z, v.y]
    for (let k = 0; k < 3; k++) { min[k] = Math.min(min[k], enu[k]); max[k] = Math.max(max[k], enu[k]) }
  }
  return { min, max }
}
const union = (bs: Box[]): Box => ({
  min: [0, 1, 2].map(k => Math.min(...bs.map(b => b.min[k]))) as Box['min'],
  max: [0, 1, 2].map(k => Math.max(...bs.map(b => b.max[k]))) as Box['max'],
})
const near = (a: number, b: number, rel = 0.05) => expect(Math.abs(a - b)).toBeLessThanOrEqual(rel * Math.max(Math.abs(b), 1))
/** The unit's own box (its pos is the centre). */
const partBox = (p: Part): Box => ({ min: [p.pos[0] - p.size[0] / 2, p.pos[1] - p.size[1] / 2, p.pos[2] - p.size[2] / 2], max: [p.pos[0] + p.size[0] / 2, p.pos[1] + p.size[1] / 2, p.pos[2] + p.size[2] / 2] })
const units = (parts: Part[]) => parts.filter(p => p.heroable === true)

describe('hero world boxes (instanceMatrix) against the parametric units', () => {
  it.each([['bess', 'container', 40], ['h2store', 'tank', 100]] as const)('%s: each %s fills its unit\'s box (east, north, up)', (id, hero, amount) => {
    const out = runTemplate(entry(id).geometry, { amount })
    const m = HERO_MODELS[hero]
    const inst = heroInstances(out.parts, m)
    units(out.parts).forEach((u, k) => {
      const b = worldBox(m, instanceMatrix(inst[k])), want = partBox(u)
      for (let a = 0; a < 3; a++) { near(b.min[a], want.min[a]); near(b.max[a], want.max[a]) }
    })
  })
  it('PV tables: the tiles of a table span it along east and north, standing on the ground', () => {
    const out = runTemplate(entry('pv').geometry, { amount: 2 })
    const m = HERO_MODELS.pvTable
    const inst = heroInstances(out.parts, m)
    const tables = units(out.parts)
    const per = inst.length / tables.length
    tables.forEach((t, k) => {
      const b = union(inst.slice(k * per, (k + 1) * per).map(i => worldBox(m, instanceMatrix(i))))
      const want = partBox(t)
      near(b.min[0], want.min[0]); near(b.max[0], want.max[0]); near(b.min[1], want.min[1]); near(b.max[1], want.max[1])
      expect(b.min[2]).toBeCloseTo(0, 6)
    })
  })
  it('a hall: its tiles cover the footprint at the hall\'s height', () => {
    const out = runTemplate(entry('load').geometry, { amount: 20 })
    const m = HERO_MODELS.hall
    const body = units(out.parts)[0], want = partBox(body)
    const boxes = heroInstances(out.parts, m).map(i => worldBox(m, instanceMatrix(i)))
    const b = union(boxes)
    for (let a = 0; a < 3; a++) { near(b.min[a], want.min[a]); near(b.max[a], want.max[a]) }
  })
  it('turbines: the model\'s hub lands on the parametric hub; the scaled blades reach the rotor radius and pivot on the hub', () => {
    const out = runTemplate(entry('wind').geometry, { amount: 15 })
    const m = HERO_MODELS.turbine, r = m.rotor!
    const radius = (entry('wind').geometry.params.rotorDiameter as number) / 2
    heroInstances(out.parts, m).forEach((i, t) => {
      const base = instanceMatrix(i)
      const hub = new THREE.Vector3(...r.hub).applyMatrix4(base)
      const want = out.anchors!.rotors![t].hub
      expect(hub.x).toBeCloseTo(want[0], 6); expect(-hub.z).toBeCloseTo(want[1], 6); expect(hub.y).toBeCloseTo(want[2], 6)
      for (const angle of [0, 1, 2]) {
        const rm = rotorMatrix(base, m, angle, i.rotorScale)
        expect(new THREE.Vector3(...r.hub).applyMatrix4(rm).distanceTo(hub)).toBeLessThan(1e-6)        // pivots on the hub
        const tip = new THREE.Vector3(r.hub[0], r.hub[1] + r.radius, r.hub[2]).applyMatrix4(rm)
        expect(tip.distanceTo(hub)).toBeCloseTo(radius, 6)                                            // rotor diameter
        const axis = new THREE.Vector3(r.hub[0] + 1, r.hub[1], r.hub[2]).applyMatrix4(rm).sub(hub).normalize()
        expect(Math.abs(axis.z)).toBeCloseTo(1, 6)                                                    // spins about north–south: the rotor faces south
      }
    })
  })
})

describe('heroPieces', () => {
  it('bakes node transforms, removes a mirroring root scale, and marks the rotor node\'s meshes', () => {
    const root = new THREE.Group(); root.scale.set(-1, 1, 1)
    const body = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial()); body.position.set(0.5, 0, 0)
    const blades = new THREE.Group(); blades.name = 'blades'; blades.position.set(0, 2, 0)
    const blade = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial())
    blades.add(blade); root.add(body, blades)
    const pieces = heroPieces(root, { ...HERO_MODELS.turbine })
    expect(pieces).toHaveLength(2)
    for (const p of pieces) expect(p.matrix.determinant()).toBeGreaterThan(0)
    expect(pieces.map(p => p.rotor)).toEqual([false, true])
    expect(new THREE.Vector3().applyMatrix4(pieces[0].matrix).x).toBeCloseTo(0.5, 6)   // the mirror is undone, the offset kept
  })
})

describe('tintMaterial', () => {
  it('a per-object copy coloured with the tint, its texture reduced to shading, one shared program', () => {
    const src = new THREE.MeshStandardMaterial({ color: '#ffffff' })
    const a = tintMaterial(src, '#0891b2'), b = tintMaterial(src, '#7c3aed')
    expect(a).not.toBe(src); expect(a).not.toBe(b)
    expect(a.color.getHexString()).toBe('0891b2')
    expect(src.color.getHexString()).toBe('ffffff')
    const shader = { fragmentShader: 'x\n#include <map_fragment>\ny' } as Parameters<THREE.Material['onBeforeCompile']>[0]
    a.onBeforeCompile(shader, undefined as never)
    expect(shader.fragmentShader).toContain(TINT_MAP_FRAGMENT)
    expect(shader.fragmentShader).not.toContain('#include <map_fragment>')
    expect(a.customProgramCacheKey()).toBe(b.customProgramCacheKey())
    expect(THREE.ShaderChunk.map_fragment).toContain('sampledDiffuseColor')   // the chunk we replace still looks as assumed
  })
})
