// SC-02b: the Model part's pure rules (UX SC-02 plan, conditions 1–6).
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { SceneAssociation, SceneBuilding, SceneTarget } from '../api/siteScene'
import type { Generator, Link, Load, StorageUnit, Store } from '../api/types'
import {
  attachCandidates, buildBindInputs, buildingName, candidatesFor, checkShares, clauseTail, featureName, findingCopy,
  findingObjects, KIND_WORDS, nextTask, NOT_CHECKED_COPY, placeableItems, placeRefusalCopy, rowCopy, shareFeatures,
  startingShares, targetDetail, targetLabel, TASK_ATTACH, TASK_PLACE,
} from './associations'

const osm: SceneBuilding = { id: 'osm:7', source: { namespace: 'osm', source_id: '7' }, footprint: [[0, 0], [10, 0], [10, 10], [0, 10]],
  height_m: 6, height_evidence: 'fallback', levels: null }
const gf: SceneBuilding = { id: 'greenfield:b1', source: { namespace: 'greenfield', source_id: 'b1' }, footprint: [[20, 0], [30, 0], [30, 10], [20, 10]],
  height_m: 9, height_evidence: 'greenfield', levels: null }
const buildings = [osm, gf]

const row = (o: Partial<SceneAssociation>): SceneAssociation => ({
  id: 'a1', feature: 'osm:7', class: 'Load', uid: 'u1', name: 'cooling_1', name_at_bind: 'cooling_1', role: 'load',
  weight: null, carrier: 'heat', unit: 'MW', status: 'bound', reason: null, rebound_from: null, ...o,
})

const rows = {
  loads: [{ name: 'cooling_1', bus: 'h', carrier: 'heat' }] as Load[],
  links: [{ name: 'haber', bus0: 'e', bus1: 'nh3', carrier: 'nh3', eh_process: true }, { name: 'tie', bus0: 'a', bus1: 'b', carrier: 'DC' }] as unknown as Link[],
  stores: [{ name: 'tank', bus: 'nh3', carrier: 'nh3' }] as Store[],
  generators: [{ name: 'pv', bus: 'e', carrier: 'solar' }] as Generator[],
  storageUnits: [{ name: 'bess', bus: 'e', carrier: 'battery' }] as StorageUnit[],
}

describe('kind words', () => {
  it('match the backend’s own word map (parity)', () => {
    const py = readFileSync(join(__dirname, '../../../backend/services/site_scene/associations.py'), 'utf8')
    const map = /KIND_WORDS = \{([^}]*)\}/.exec(py)![1]
    for (const cls of ['Load', 'Store', 'Generator', 'StorageUnit'] as const) {
      expect(map).toContain(`"${cls}": "${KIND_WORDS[cls]}"`)
    }
    // A process Link reads "process unit" in the backend's `_label`.
    expect(py).toContain("'process unit' if process")
    expect(KIND_WORDS.Link).toBe('process unit')
  })

  it('Guided names with a kind word; Expert with the class; never Class:name', () => {
    expect(targetLabel('Load', 'cooling_1', 'guided')).toBe('cooling_1 · load')
    expect(targetLabel('Load', 'cooling_1', 'expert')).toBe('cooling_1 · Load')
    expect(targetLabel('Link', 'haber', 'guided')).toBe('haber · process unit')
    expect(targetLabel('Load', 'cooling_1', 'guided')).not.toContain('Load:')
  })
})

describe('candidates', () => {
  it('loads, process units only (not every link), stores, generators, storage units', () => {
    expect(attachCandidates(rows).map(c => `${c.class}:${c.name}`))
      .toEqual(['Load:cooling_1', 'Link:haber', 'Store:tank', 'Generator:pv', 'StorageUnit:bess'])
    expect(placeableItems(rows).map(c => c.name)).toEqual(['haber', 'tank', 'pv', 'bess'])
  })

  it('a building’s picker leaves out what it is already attached to', () => {
    const all = attachCandidates(rows)
    expect(candidatesFor('osm:7', all, [row({})]).map(c => c.name)).not.toContain('cooling_1')
    expect(candidatesFor('greenfield:b1', all, [row({})]).map(c => c.name)).toContain('cooling_1')
  })
})

describe('names', () => {
  it('buildings by how the user knows them; groups by label; placement keys by name', () => {
    expect(buildingName('osm:7', buildings)).toBe('OSM building 7')
    expect(buildingName('greenfield:b1', buildings)).toBe('Building 2')
    expect(featureName('g1', buildings, [{ id: 'g1', label: 'North complex', members: [] }])).toBe('North complex')
    expect(featureName('Generator:pv', buildings)).toBe('pv')
  })

  it('a server reason after a colon starts in lower case unless it is a name', () => {
    expect(clauseTail('No heat load in the model')).toBe('no heat load in the model')
    expect(clauseTail('cooling_1 · load was deleted or replaced')).toBe('cooling_1 · load was deleted or replaced')
  })
})

describe('row copy', () => {
  it('bound: name · kind · carrier · unit', () => {
    expect(rowCopy(row({}), 'guided', true)).toEqual({ text: 'cooling_1 · load · Heat · MW', fix: null, tone: 'ok' })
    expect(rowCopy(row({}), 'expert', true).text).toBe('cooling_1 · Load · Heat · MW')
  })

  it('unbound: "Not attached: …" with › Attach again only when a rebind can fix it', () => {
    const a = row({ status: 'unbound', name: null, reason: 'cooling_1 · load was deleted or replaced' })
    expect(rowCopy(a, 'guided', true)).toEqual({ text: 'Not attached: cooling_1 · load was deleted or replaced', fix: 'attach_again', tone: 'warn' })
    expect(rowCopy(a, 'guided', false).fix).toBeNull()
  })

  it('unavailable: "Unavailable: …" with › Loads', () => {
    const a = row({ status: 'unavailable', uid: null, name: null, reason: 'No heat load in the model' })
    expect(rowCopy(a, 'guided', true)).toEqual({ text: 'Unavailable: no heat load in the model', fix: 'loads', tone: 'warn' })
  })

  it('not checked: no link', () => {
    expect(rowCopy(row({ status: 'not_checked', name: null }), 'expert', true)).toEqual({ text: NOT_CHECKED_COPY, fix: null, tone: 'muted' })
  })

  it('every copy uses a colon and no em dash or Class:name ref', () => {
    for (const s of ['bound', 'unbound', 'unavailable', 'not_checked'] as const) {
      const t = rowCopy(row({ status: s, name: s === 'bound' ? 'cooling_1' : null, reason: s === 'unavailable' ? 'No heat load in the model' : null }), 'guided', true).text
      expect(t).not.toMatch(/—|Load:|stale/)
    }
  })
})

describe('shares', () => {
  const target = { class: 'Load' as const, name: 'cooling_1' }
  const current = [row({ id: 'a1', feature: 'osm:7', weight: 0.6 })]

  it('the features a target is split over, and their starting %', () => {
    expect(shareFeatures(current, target, 'greenfield:b1')).toEqual(['osm:7', 'greenfield:b1'])
    expect(startingShares(current, target, ['osm:7', 'greenfield:b1'])).toEqual({ 'osm:7': 60, 'greenfield:b1': null })
  })

  it('none, partial, off and 100 %, each in words', () => {
    const f = ['osm:7', 'greenfield:b1']
    expect(checkShares({}, f)).toMatchObject({ state: 'none', copy: 'Not split: every building counts the whole item' })
    expect(checkShares({ 'osm:7': 60 }, f)).toMatchObject({ state: 'partial', copy: 'Give every building a share, or none' })
    expect(checkShares({ 'osm:7': 60, 'greenfield:b1': 25 }, f)).toMatchObject({ state: 'off', copy: 'Total 85 %: shares should add up to 100 %' })
    expect(checkShares({ 'osm:7': 60, 'greenfield:b1': 40 }, f)).toMatchObject({ state: 'ok', copy: 'Total 100 %' })
    expect(checkShares({ 'osm:7': 0, 'greenfield:b1': 100 }, f).state).toBe('invalid')
    expect(checkShares({ a: 10, b: 20, c: 70 }, ['a', 'b', 'c']).state).toBe('ok')
  })
})

describe('bind_targets inputs', () => {
  const pv = { class: 'Generator' as const, name: 'pv', carrier: 'solar' }
  const cooling = { class: 'Load' as const, name: 'cooling_1', carrier: 'heat' }

  it('keeps every current row by id and adds the new one', () => {
    const current = [row({ id: 'a1' }), row({ id: 'a2', status: 'unbound', name: null })]
    expect(buildBindInputs(current, { feature: 'osm:7', target: pv, shares: {} })).toEqual({
      associations: [{ id: 'a1' }, { id: 'a2' }, { feature: 'osm:7', target: { class: 'Generator', name: 'pv' } }],
      groups: [],
    })
  })

  it('a split restates the target’s rows with their weights as fractions', () => {
    const current = [row({ id: 'a1', feature: 'osm:7' }), row({ id: 'a9', class: 'Generator', name: 'pv', feature: 'osm:7' })]
    const inputs = buildBindInputs(current, { feature: 'greenfield:b1', target: cooling, shares: { 'osm:7': 70, 'greenfield:b1': 30 } })
    expect(inputs.associations).toEqual([
      { feature: 'osm:7', target: { class: 'Load', name: 'cooling_1' }, weight: 0.7 },
      { id: 'a9' },
      { feature: 'greenfield:b1', target: { class: 'Load', name: 'cooling_1' }, weight: 0.3 },
    ])
  })

  it('no shares clears an earlier split, so the target is not half-split', () => {
    const current = [row({ id: 'a1', feature: 'osm:7', weight: 1 })]
    expect(buildBindInputs(current, { feature: 'greenfield:b1', target: cooling, shares: {} }).associations).toEqual([
      { feature: 'osm:7', target: { class: 'Load', name: 'cooling_1' } },
      { feature: 'greenfield:b1', target: { class: 'Load', name: 'cooling_1' } },
    ])
  })
})

describe('next task (Guided)', () => {
  it('attach first, then place equipment', () => {
    expect(nextTask([])).toBe(TASK_ATTACH)
    expect(nextTask([row({ status: 'unbound', name: null })])).toBe(TASK_ATTACH)
    expect(nextTask([row({})])).toBe(TASK_PLACE)
    expect(TASK_ATTACH).toBe('Attach buildings to loads and processes')
  })
})

describe('Expert detail', () => {
  const t = (o: Partial<SceneTarget>): SceneTarget => ({
    class: 'Load', name: 'cooling_1', features: ['osm:7', 'greenfield:b1'], enclosures: 2,
    capacity: { value: 3, available: true, reason: null, unit: 'MW' }, allocation: { basis: 'none', shares: [] }, ...o,
  })

  it('enclosures, capacity once, and "not split" without shares', () => {
    expect(targetDetail(t({}), buildings)).toEqual({ enclosures: 'Enclosures 2', capacity: 'Capacity 3 MW', shares: ['Not split: each building counts the whole item'] })
  })

  it('shares as % with the allocated value and unit', () => {
    const d = targetDetail(t({ allocation: { basis: 'explicit', shares: [
      { feature: 'osm:7', weight: 0.4, allocated: { value: 1.2, available: true, reason: null, unit: 'MW' } },
      { feature: 'greenfield:b1', weight: 0.6, allocated: { value: 1.8, available: true, reason: null, unit: 'MW' } },
    ] } }), buildings)
    expect(d.shares).toEqual(['OSM building 7: 40 % · 1.2 MW allocated', 'Building 2: 60 % · 1.8 MW allocated'])
  })

  it('a time-varying capacity says so', () => {
    expect(targetDetail(t({ enclosures: 1, capacity: { value: null, available: false, reason: 'time_varying_demand', unit: 'MW' } }), buildings).capacity)
      .toBe('Capacity varies over time')
  })
})

describe('findings and refusals in plain words', () => {
  const names = { item: (k: string) => k.split(':')[1], building: (id: string) => buildingName(id, buildings) }

  it('overlap names the building as the user knows it; selects both objects', () => {
    const f = { code: 'overlap', severity: 'warn' as const, objects: ['StorageUnit:bess', 'osm:7'], message: 'bess · storage unit overlaps building osm:7' }
    expect(findingCopy(f, names)).toBe('Overlaps OSM building 7')
    expect(findingObjects(f, buildings)).toEqual({ items: ['StorageUnit:bess'], buildings: ['osm:7'] })
  })

  it('double-counted demand is shown as the server words it', () => {
    const message = 'cooling_1 · load and haber · process unit both draw electricity here: this demand may be counted twice'
    expect(findingCopy({ code: 'double_counted_demand', severity: 'warn', message, objects: ['Link:haber', 'Load:cooling_1'] }, names)).toBe(message)
  })

  it('off the roof and not for a roof', () => {
    expect(placeRefusalCopy('off_roof', 'pv · generator', 'OSM building 7')).toBe('Off the roof of OSM building 7')
    expect(placeRefusalCopy('not_roof_eligible', 'bess · storage unit', null)).toBe('Can’t go on a roof: bess · storage unit')
    expect(placeRefusalCopy('unbound_target', 'x', null)).toBeNull()
  })
})

describe('the 3D scene’s inputs', () => {
  it('buildings become named obstacles', async () => {
    const { sceneObstacles } = await import('./associations')
    expect(sceneObstacles(buildings)).toEqual([
      { id: 'osm:7', label: 'OSM building 7', footprint: osm.footprint, height_m: 6 },
      { id: 'greenfield:b1', label: 'Building 2', footprint: gf.footprint, height_m: 9 },
    ])
  })

  it('a scene roof placement carries its on; positions stay the Site’s; no scene, no change', async () => {
    const { withSceneRoofs } = await import('./associations')
    const site: Record<string, { x: number; y: number; heading: number; on?: string }> = { 'Generator:pv': { x: 1, y: 2, heading: 0 }, 'StorageUnit:bess': { x: 5, y: 5, heading: 0 } }
    expect(withSceneRoofs(site, undefined)).toBe(site)
    expect(withSceneRoofs(site, { 'Generator:pv': { x: 9, y: 9, heading: 0, on: 'roof:osm:7' } })).toEqual({
      'Generator:pv': { x: 1, y: 2, heading: 0, on: 'roof:osm:7' }, 'StorageUnit:bess': { x: 5, y: 5, heading: 0 },
    })
    expect(withSceneRoofs(site, { 'StorageUnit:bess': { x: 5, y: 5, heading: 0, on: 'ground' } })['StorageUnit:bess'].on).toBe('ground')
  })
})

describe('shares: review fixes', () => {
  it('stored thirds prefill unrounded and pass the backend’s own tolerance', () => {
    const third = 1 / 3
    const current = ['osm:7', 'greenfield:b1', 'g3'].map((f, i) => row({ id: `a${i}`, feature: f, weight: third }))
    const t = { class: 'Load' as const, name: 'cooling_1' }
    const f = ['osm:7', 'greenfield:b1', 'g3']
    const start = startingShares(current, t, f)
    expect(start['osm:7']).toBeCloseTo(33.3333333, 6)
    expect(checkShares(start, f).state).toBe('ok')
    // 33.33 + 33.33 + 33.34 is 100 within 1e-9 as fractions.
    expect(checkShares({ a: 33.33, b: 33.33, c: 33.34 }, ['a', 'b', 'c']).state).toBe('ok')
  })

  it('a total that only rounds to 100 % is off, and shows its digits', () => {
    const c = checkShares({ a: 50, b: 49.9999 }, ['a', 'b'])
    expect(c.state).toBe('off')
    expect(c.copy).toBe('Total 99.9999 %: shares should add up to 100 %')
  })

  it('the picker for a placed key lists every item of its class, any Link included', async () => {
    const { sameClassItems } = await import('./associations')
    expect(sameClassItems(rows, 'Link').map(c => c.name)).toEqual(['haber', 'tie'])
    expect(sameClassItems(rows, 'Bus')).toEqual([])
  })
})
