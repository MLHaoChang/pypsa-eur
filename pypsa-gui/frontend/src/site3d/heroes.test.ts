// Phase 2 plan Tasks 3.1–3.3: the hero model files, their provenance, and
// fitting them onto the parametric units they replace. Reads the GLB JSON
// chunk directly (GLTFLoader cannot parse in jsdom).
import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { gzipSync } from 'node:zlib'
import { HERO_MODELS, HERO_IDS, heroInstances, instanceBox, heroUrl, type HeroModel } from './heroes'
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
  it('the turbine\'s blades node is where the manifest pivots it, with no scale in its chain', () => {
    const j = readGlbJson(new Uint8Array(glb(HERO_MODELS.turbine)))
    const blades = j.nodes.find(n => n.name === 'blades')!
    expect(blades.translation!.map(v => +v.toFixed(3))).toEqual(HERO_MODELS.turbine.rotor!.hub)
    expect(blades.scale ?? [1, 1, 1]).toEqual([1, 1, 1])
    expect(j.nodes[j.scenes[0].nodes[0]].scale ?? [1, 1, 1]).toEqual([1, 1, 1])
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

// ── fitting ──────────────────────────────────────────────────────────────────

const within = (a: number, b: number, rel = 0.05) => Math.abs(a - b) <= rel * Math.max(Math.abs(b), 1)

describe('heroInstances', () => {
  it('containers: one per container, the box of a 20 ft unit (east–west), not mirrored or oversized', () => {
    const out = runTemplate(entry('bess').geometry, { amount: 40 })
    const units = out.parts.filter(p => p.heroable)
    const inst = heroInstances(out.parts, HERO_MODELS.container)
    expect(inst).toHaveLength(units.length)
    inst.forEach((i, k) => {
      const b = instanceBox(i, HERO_MODELS.container), u = units[k]
      expect(within(b.max[0] - b.min[0], u.size[0])).toBe(true)     // 6.1 m east
      expect(within(b.max[1] - b.min[1], u.size[1])).toBe(true)     // 2.44 m north
      expect(within(b.max[2] - b.min[2], u.size[2])).toBe(true)     // 2.9 m high
      expect(i.scale.every(v => v > 0)).toBe(true)
      expect(within((b.min[0] + b.max[0]) / 2, u.pos[0])).toBe(true)
    })
  })
  it('turbines: the hub lands at the hub height, south of the tower (rotor facing south)', () => {
    const out = runTemplate(entry('wind').geometry, { amount: 15 })
    const inst = heroInstances(out.parts, HERO_MODELS.turbine)
    expect(inst).toHaveLength(3)
    const hubH = entry('wind').geometry.params.hubHeight as number
    for (const i of inst) {
      expect(within(i.hub![2], hubH)).toBe(true)
      expect(i.hub![1]).toBeLessThan(i.pos[1])           // south of the tower
      expect(Math.abs(i.hub![0] - i.pos[0])).toBeLessThan(1)
      expect(i.scale[0]).toBe(i.scale[1])                // uniform
    }
  })
  it('tanks: one per bullet, lying north, the bullet\'s box', () => {
    const out = runTemplate(entry('h2store').geometry, { amount: 100 })
    const units = out.parts.filter(p => p.heroable)
    const inst = heroInstances(out.parts, HERO_MODELS.tank)
    expect(inst).toHaveLength(units.length)
    const b = instanceBox(inst[0], HERO_MODELS.tank)
    expect(within(b.max[1] - b.min[1], 20)).toBe(true)
    expect(within(b.max[0] - b.min[0], 3)).toBe(true)
  })
  it('PV tables are tiled along each table without re-tilting, covering its length', () => {
    const out = runTemplate(entry('pv').geometry, { amount: 2 })
    const tables = out.parts.filter(p => p.heroable)
    const inst = heroInstances(out.parts, HERO_MODELS.pvTable)
    expect(inst.length).toBeGreaterThan(tables.length)
    const perTable = inst.length / tables.length
    const span = inst.slice(0, perTable).map(i => instanceBox(i, HERO_MODELS.pvTable))
    const east = Math.max(...span.map(b => b.max[0])) - Math.min(...span.map(b => b.min[0]))
    expect(within(east, tables[0].size[0])).toBe(true)
    expect(within(span[0].max[1] - span[0].min[1], tables[0].size[1])).toBe(true)
  })
  it('a hall is tiled to its footprint at its own height (no tall stretch)', () => {
    const out = runTemplate(entry('load').geometry, { amount: 20 })
    const body = out.parts.find(p => p.heroable)!
    const inst = heroInstances(out.parts, HERO_MODELS.hall)
    const boxes = inst.map(i => instanceBox(i, HERO_MODELS.hall))
    const w = Math.max(...boxes.map(b => b.max[0])) - Math.min(...boxes.map(b => b.min[0]))
    const d = Math.max(...boxes.map(b => b.max[1])) - Math.min(...boxes.map(b => b.min[1]))
    expect(within(w, body.size[0])).toBe(true)
    expect(within(d, body.size[1])).toBe(true)
    for (const b of boxes) expect(within(b.max[2] - b.min[2], body.size[2])).toBe(true)
    for (const i of inst) expect(Math.max(...i.scale) / Math.min(...i.scale)).toBeLessThan(2)
  })
  it('only heroable units are instanced (PCS skids, stacks, BoP stay parametric)', () => {
    const out = runTemplate(entry('bess').geometry, { amount: 40 })
    expect(heroInstances(out.parts, HERO_MODELS.container)).toHaveLength(10)
  })
})
