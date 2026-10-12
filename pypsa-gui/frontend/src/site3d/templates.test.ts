// Phase 2 plan Task 1.3: geometry templates (pure, no three).
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { runTemplate, TEMPLATE_ANCHORS, type TemplateOutput } from './templates'
import { DEFAULT_LIBRARY } from './assetLibrary'

const entry = (id: string) => DEFAULT_LIBRARY.find(t => t.id === id)!
const run = (id: string, amount: number, extra: Record<string, unknown> = {}): TemplateOutput =>
  runTemplate(entry(id).geometry, { amount, ...extra })

describe('tankArray', () => {
  it('H₂ bullets: horizontal cylinders along north, one per unit, on the ground', () => {
    const out = run('h2store', 100)   // 100 MWh at 20 MWh per bullet = 5
    const tanks = out.parts.filter(p => p.shape === 'cylinder')
    expect(tanks).toHaveLength(5)
    for (const t of tanks) {
      expect(t.axis).toBe('north')
      expect(t.size[0]).toBe(t.size[2])              // [d, L, d]
      expect(t.size[1]).toBeGreaterThan(t.size[0])
      expect(t.pos[2]).toBeCloseTo(t.size[2] / 2, 6) // resting on the ground
    }
  })
  it('thermal store: vertical tanks [d, d, h] standing on the ground', () => {
    const out = run('thermalStore', 500)
    const tanks = out.parts.filter(p => p.shape === 'cylinder')
    expect(tanks.length).toBeGreaterThan(0)
    for (const t of tanks) {
      expect(t.axis).toBe('up')
      expect(t.size[0]).toBe(t.size[1])
      expect(t.pos[2]).toBeCloseTo(t.size[2] / 2, 6)
    }
  })
  it('the packer pitch uses size[0] × size[1], so the footprint grows with count', () => {
    const one = run('h2store', 20), six = run('h2store', 120), seven = run('h2store', 140)
    expect(six.footprint[0]).toBeGreaterThan(one.footprint[0])
    expect(seven.footprint[1]).toBeGreaterThan(six.footprint[1])  // a second row
  })
})

describe('turbineArray', () => {
  it('the tower is a cylinder; each turbine has its own rotor parts and hub', () => {
    const out = run('wind', 15)   // 3 × 5 MW
    const towers = out.parts.filter(p => p.shape === 'cylinder' && p.axis === 'up')
    expect(towers).toHaveLength(3)
    const rotors = out.parts.filter(p => p.anchor === 'rotor')
    expect(rotors).toHaveLength(9)
    expect(new Set(rotors.map(p => p.turbine))).toEqual(new Set([0, 1, 2]))
    expect(out.anchors.rotors).toHaveLength(3)
    const hubs = out.anchors.rotors!.map(r => r.hub.join(','))
    expect(new Set(hubs).size).toBe(3)
  })
  it('is capped at 120 turbines drawn, and says so', () => {
    const out = run('wind', 5 * 500)
    expect(out.anchors.rotors!.length).toBeLessThanOrEqual(120)
    expect(out.each).toBeGreaterThan(1)
    expect(out.count).toBe(500)
  })
})

describe('unitGrid', () => {
  it('BESS: containers plus one PCS skid per row (the Phase 1 count)', () => {
    const out = run('bess', 40)   // 10 containers, 8 per row → 2 rows
    expect(out.parts).toHaveLength(12)
    expect(out.parts.filter(p => p.heroable)).toHaveLength(10)
  })
  it('gensets: an exhaust stack per row, as a cylinder', () => {
    const out = run('thermal', 30)   // 12 enclosures, 6 per row
    const stacks = out.parts.filter(p => p.shape === 'cylinder')
    expect(stacks).toHaveLength(2)
  })
})

describe('hall, reservoir, pvRoof', () => {
  it('a hall is never smaller than its minimum area', () => {
    expect(run('load', 0).areaM2).toBeGreaterThanOrEqual(200)
  })
  it('the reservoir takes land for its basin', () => {
    const out = run('pumpedHydro', 600)
    expect(out.areaM2).toBeGreaterThan(0)
    expect(out.areaM2).toBeGreaterThanOrEqual(out.footprint[0] * out.footprint[1] * 0.5)
  })
  it('rooftop PV takes no land', () => {
    expect(run('pvRoof', 2).areaM2).toBe(0)
  })
})

describe('parts stay inside the footprint (WP1 review gate)', () => {
  it.each(DEFAULT_LIBRARY.map(t => t.id))('%s', id => {
    for (const amount of [0.5, 10, 400, 5000]) {
      const out = runTemplate(entry(id).geometry, { amount, bays: 3, vNom: 110 })
      const [w, d] = out.footprint
      for (const q of out.parts) {
        const tol = 0.5
        expect(Math.abs(q.pos[0]) + q.size[0] / 2, `${id} @ ${amount}: east extent`).toBeLessThanOrEqual(w / 2 + tol)
        expect(Math.abs(q.pos[1]) + q.size[1] / 2, `${id} @ ${amount}: north extent`).toBeLessThanOrEqual(d / 2 + tol)
      }
    }
  })
})

describe('Phase 1 geometry kept where the type did not change', () => {
  it('one H₂ bullet: 3 × 20 m, 60 m², and the Phase 1 summary wording', () => {
    const one = run('h2store', 20)
    expect(one.footprint).toEqual([3, 20])
    expect(one.areaM2).toBe(60)
    const huge = entry('h2store').summary({ cls: 'Store', name: 'x', carrier: 'H2', amount: 1e5, unit: 'MWh', count: 5000, each: 42, areaM2: 1, params: entry('h2store').geometry.params })
    expect(huge).toContain('(each = 42)')
  })
  it('the wind summary reads the entry\'s own unit rating and sizes', () => {
    const t = entry('wind')
    const txt = t.summary({ cls: 'Generator', name: 'w', carrier: 'onwind', amount: 9, unit: 'MW', count: 3, each: 1, areaM2: 1, params: { ...t.geometry.params, per: 3, hubHeight: 80, rotorDiameter: 90 } })
    expect(txt).toContain('3 × 3 MW, 80 m hub, 90 m rotor')
  })
})

describe('reservoir land', () => {
  it('covers the basin the energy needs, plus the powerhouse', () => {
    const p = entry('pumpedHydro').geometry.params as Record<string, number>
    const out = run('pumpedHydro', 600)
    expect(out.areaM2).toBeGreaterThanOrEqual(600 * p.m2PerMwh + p.powerhouseW * p.powerhouseD)
    expect(out.areaM2).toBeLessThanOrEqual(out.footprint[0] * out.footprint[1])
  })
})

describe('anchors', () => {
  it('a unit grid provides the anchor its entry asks for', () => {
    for (const t of DEFAULT_LIBRARY.filter(x => x.geometry.template === 'unitGrid')) {
      const out = runTemplate(t.geometry, { amount: 10 })
      if (t.geometry.params.anchor === 'fill') expect(out.anchors.fill, t.id).toBeDefined()
      if (t.geometry.params.anchor === 'emissive') expect(out.anchors.emissive, t.id).toBe(true)
    }
  })
  it('the fill gauge lies on top of the units, inside the footprint', () => {
    for (const id of ['bess', 'h2store', 'thermalStore', 'flywheel']) {
      const out = run(id, 100)
      const g = out.anchors.fill!
      expect(Math.abs(g.pos[0]) + g.size[0] / 2, id).toBeLessThanOrEqual(out.footprint[0] / 2)
      expect(Math.abs(g.pos[1]) + g.size[1] / 2, id).toBeLessThanOrEqual(out.footprint[1] / 2)
    }
  })

  it('every template declares its animation anchors', () => {
    for (const t of DEFAULT_LIBRARY) {
      const declared = TEMPLATE_ANCHORS[t.geometry.template]
      expect(declared, t.geometry.template).toBeDefined()
      const out = runTemplate(t.geometry, { amount: 10 })
      if (declared.includes('fill')) expect(out.anchors.fill, t.id).toBeDefined()
      if (declared.includes('rotor')) expect(out.anchors.rotors?.length, t.id).toBeGreaterThan(0)
      if (declared.includes('flow')) expect(out.anchors.flow, t.id).toBeDefined()
      if (declared.includes('emissive')) expect(out.anchors.emissive, t.id).toBe(true)
    }
  })
})

describe('layout.ts source guards', () => {
  const src = readFileSync(join(__dirname, 'layout.ts'), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '')          // block comments
    .replace(/(^|[^:])\/\/.*$/gm, '$1')         // line comments
  it('names no carrier in a regex literal', () => {
    const regexLiterals = src.match(/\/(?![*/])(?:\\.|[^/\n])+\/[a-z]*(?=[.,;)\]}\s])/g) ?? []
    for (const re of regexLiterals) expect(re).not.toMatch(/solar|wind|electroly|h2|hydrogen|batter|pv|heat|gas|ccgt|hydro|boiler/i)
  })
  it('holds no colours', () => {
    expect(src).not.toMatch(/#[0-9a-f]{6}\b/i)
  })
  it('has no module-level mutable state', () => {
    expect(src).not.toMatch(/^let\s/m)
  })
})
