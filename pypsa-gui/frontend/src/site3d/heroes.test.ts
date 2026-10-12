// Phase 2 plan Tasks 3.1–3.3: the hero model files, their provenance, and
// fitting them onto the parametric units they replace. Reads the GLB JSON
// chunk directly (GLTFLoader cannot parse in jsdom).
import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { gzipSync } from 'node:zlib'
import { HERO_MODELS, HERO_IDS, MAX_HERO_INSTANCES, heroInstances, heroUrl, type HeroModel } from './heroes'
import { readGlbJson, type GltfJson } from './glbJson'
import { runTemplate } from './templates'
import { DEFAULT_LIBRARY } from './assetLibrary'

const DIR = join(__dirname, '..', '..', 'public', 'site3d', 'models')
const glb = (m: HeroModel) => readFileSync(join(DIR, m.file))
const entry = (id: string) => DEFAULT_LIBRARY.find(t => t.id === id)!

/** The model's bounds after its node transforms (translation and scale only — none of these files rotates), mirror removed. */
function measuredBounds(j: GltfJson): { min: number[]; max: number[] } {
  const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity]
  const walk = (i: number, t: number[], s: number[]) => {
    const n = j.nodes[i]
    const ns = (n.scale ?? [1, 1, 1]).map(Math.abs)
    const s2 = s.map((v, k) => v * ns[k])
    const t2 = t.map((v, k) => v + (n.translation?.[k] ?? 0) * s[k])
    if (n.mesh != null) {
      const acc = j.accessors[j.meshes[n.mesh].primitives[0].attributes.POSITION]
      for (let k = 0; k < 3; k++) {
        min[k] = Math.min(min[k], t2[k] + acc.min![k] * s2[k])
        max[k] = Math.max(max[k], t2[k] + acc.max![k] * s2[k])
      }
    }
    for (const c of n.children ?? []) walk(c, t2, s2)
  }
  for (const r of j.scenes[j.scene ?? 0].nodes) walk(r, [0, 0, 0], [1, 1, 1])
  return { min, max }
}

describe('model files and provenance', () => {
  it.each(HERO_IDS)('%s: a GLB within budget, bounds and root as the manifest says', id => {
    const m = HERO_MODELS[id]
    const bytes = glb(m)
    expect(bytes.length).toBeLessThanOrEqual(64 * 1024)
    const j = readGlbJson(new Uint8Array(bytes))
    const root = j.nodes[j.scenes[j.scene ?? 0].nodes[0]]
    expect(root.scale ?? [1, 1, 1]).toEqual(m.rootScale)
    const b = measuredBounds(j)
    for (let k = 0; k < 3; k++) {
      expect(b.min[k]).toBeCloseTo(m.bounds.min[k], 3)
      expect(b.max[k]).toBeCloseTo(m.bounds.max[k], 3)
    }
    // Uncompressed geometry: no decoder is ever needed (or fetched).
    expect(j.extensionsRequired ?? []).not.toContain('KHR_draco_mesh_compression')
    expect(j.extensionsRequired ?? []).not.toContain('EXT_meshopt_compression')
  })
  it('the turbine\'s blades node is where the manifest pivots it, unrotated (spin axis = local X), unscaled, with the manifest\'s tip radius', () => {
    const j = readGlbJson(new Uint8Array(glb(HERO_MODELS.turbine)))
    const blades = j.nodes.find(n => n.name === 'blades')!
    const rotor = HERO_MODELS.turbine.rotor!
    blades.translation!.forEach((v, k) => expect(v).toBeCloseTo(rotor.hub[k], 6))
    expect(blades.rotation ?? [0, 0, 0, 1]).toEqual([0, 0, 0, 1])
    expect(blades.matrix).toBeUndefined()
    expect(blades.scale ?? [1, 1, 1]).toEqual([1, 1, 1])
    expect(j.nodes[j.scenes[0].nodes[0]].scale ?? [1, 1, 1]).toEqual([1, 1, 1])
    // The blades turn in the YZ plane: they are thin along X, and their tip is the radius.
    const acc = j.accessors[j.meshes[blades.mesh!].primitives[0].attributes.POSITION]
    expect(acc.max![0] - acc.min![0]).toBeLessThan(0.2 * (acc.max![1] - acc.min![1]))
    expect(Math.max(acc.max![1], -acc.min![1], acc.max![2], -acc.min![2])).toBeCloseTo(rotor.radius, 2)
  })
  it('the PV table\'s panels face south once yawed (the parametric tables tilt toward the equator)', () => {
    // Read the NORMALs from the BIN chunk: the tilted, upward faces are the panels.
    const bytes = glb(HERO_MODELS.pvTable)
    const j = readGlbJson(new Uint8Array(bytes)) as GltfJson & { bufferViews: { byteOffset?: number; byteStride?: number }[]; accessors: { bufferView?: number; byteOffset?: number; count: number }[] }
    expect(j.nodes.every(n => !n.rotation && !n.matrix)).toBe(true)     // mesh space = model space
    const jsonLen = bytes.readUInt32LE(12), bin = 20 + jsonLen + 8
    const yaw = HERO_MODELS.pvTable.yawDeg * Math.PI / 180
    let south = 0, north = 0
    for (const mesh of j.meshes) for (const prim of mesh.primitives) {
      const a = j.accessors[prim.attributes.NORMAL], bv = j.bufferViews[a.bufferView!]
      const off = bin + (bv.byteOffset ?? 0) + (a.byteOffset ?? 0), stride = bv.byteStride ?? 12
      for (let i = 0; i < a.count; i++) {
        const x = bytes.readFloatLE(off + i * stride), y = bytes.readFloatLE(off + i * stride + 4), z = bytes.readFloatLE(off + i * stride + 8)
        if (y < 0.3 || Math.abs(z) < 0.2) continue                         // not a tilted, upward face
        const zScene = -x * Math.sin(yaw) + z * Math.cos(yaw)              // three's Y rotation
        if (-zScene < 0) south++; else north++                             // north = −z
      }
    }
    expect(south).toBeGreaterThan(5 * north)
  })
  it('all files together stay small, and the licence and README are there', () => {
    const files = readdirSync(DIR).filter(f => f.endsWith('.glb'))
    expect(files.sort()).toEqual(HERO_IDS.map(id => HERO_MODELS[id].file).sort())
    const raw = files.reduce((a, f) => a + statSync(join(DIR, f)).size, 0)
    const gz = files.reduce((a, f) => a + gzipSync(readFileSync(join(DIR, f))).length, 0)
    expect(raw).toBeLessThanOrEqual(200 * 1024)
    expect(gz).toBeLessThanOrEqual(100 * 1024)
    const readme = readFileSync(join(DIR, 'README.md'), 'utf8')
    expect(readme).toContain('https://kenney.nl/assets/city-kit-industrial')
    expect(readme).toContain('gltf-transform')
    expect(readme).toMatch(/CC0/)
    expect(readFileSync(join(DIR, 'LICENSE-Kenney.txt'), 'utf8')).toContain('Creative Commons Zero')
  })
  it('URLs are relative to the app base, never absolute or remote', () => {
    for (const id of HERO_IDS) {
      const u = heroUrl(HERO_MODELS[id], '/')
      expect(u).toBe(`/site3d/models/${HERO_MODELS[id].file}`)
      expect(u).not.toMatch(/^https?:/)
    }
    expect(heroUrl(HERO_MODELS.tank, '/app/')).toBe('/app/site3d/models/detail-tank.glb')
  })
})

// ── fitting: which units, how many, where (the world boxes are checked
// through the real matrices in heroLoader.test.ts) ─────────────────────────

describe('heroInstances', () => {
  it('containers: one per container unit (PCS skids stay parametric), upright, not mirrored', () => {
    const out = runTemplate(entry('bess').geometry, { amount: 40 })
    const inst = heroInstances(out.parts, HERO_MODELS.container)
    expect(inst).toHaveLength(10)
    expect(inst).toHaveLength(out.parts.filter(p => p.heroable === true).length)
    for (const i of inst) expect(i.scale.every(v => v > 0)).toBe(true)
  })
  it('turbines: one per tower, uniform scale, the hub on the parametric rotor\'s hub, blades scaled to the library\'s rotor', () => {
    const out = runTemplate(entry('wind').geometry, { amount: 15 })
    const inst = heroInstances(out.parts, HERO_MODELS.turbine)
    expect(inst).toHaveLength(3)
    const params = entry('wind').geometry.params
    inst.forEach((i, t) => {
      expect(i.scale[0]).toBe(i.scale[1]); expect(i.scale[1]).toBe(i.scale[2])
      out.anchors!.rotors![t].hub.forEach((v, k) => expect(i.hub![k]).toBeCloseTo(v, 9))
      expect(i.hub![2]).toBe(params.hubHeight)
      expect(i.rotorScale! * HERO_MODELS.turbine.rotor!.radius * i.scale[0]).toBeCloseTo((params.rotorDiameter as number) / 2, 6)
    })
  })
  it('tanks: one per bullet', () => {
    const out = runTemplate(entry('h2store').geometry, { amount: 100 })
    expect(heroInstances(out.parts, HERO_MODELS.tank)).toHaveLength(out.parts.filter(p => p.heroable === true).length)
  })
  it('PV tables: tiled along each table; ground tables stand on the ground, canopy tables on the canopy deck', () => {
    const field = runTemplate(entry('pv').geometry, { amount: 2 })
    const inst = heroInstances(field.parts, HERO_MODELS.pvTable)
    expect(inst.length).toBeGreaterThan(field.parts.filter(p => p.heroable === true).length)
    for (const i of inst) expect(i.pos[2]).toBe(0)
    const canopy = runTemplate(entry('pvRoof').geometry, { amount: 1 })      // no roof given: a canopy
    const lift = entry('pvRoof').geometry.params.canopyHeight as number
    for (const i of heroInstances(canopy.parts, HERO_MODELS.pvTable)) expect(i.pos[2]).toBe(lift)
    expect(canopy.parts.filter(p => !p.heroable)).toHaveLength(4)            // the posts stay
  })
  it.each([['pv', 'pvTable', 500], ['load', 'hall', 500]] as const)('%s at %s MW: at most MAX_HERO_INSTANCES tiles, still covering every unit', (id, hero, mw) => {
    const out = runTemplate(entry(id).geometry, { amount: mw })
    const units = out.parts.filter(p => p.heroable === true)
    const inst = heroInstances(out.parts, HERO_MODELS[hero])
    expect(inst.length).toBeLessThanOrEqual(MAX_HERO_INSTANCES)
    expect(inst.length).toBeGreaterThanOrEqual(units.length)
  })
  it('a hall hides its flat-roof plant strip under a hero (it would float over the pitched roof), and only the hall body is tiled', () => {
    const out = runTemplate(entry('load').geometry, { amount: 20 })
    const strip = out.parts.find(p => p.heroable === 'hide')
    expect(strip).toBeDefined()
    const inst = heroInstances(out.parts, HERO_MODELS.hall)
    const body = out.parts.find(p => p.heroable === true)!
    for (const i of inst) expect(Math.abs(i.pos[1] - body.pos[1])).toBeLessThanOrEqual(body.size[1] / 2)
  })
})
