// SC-03 (D07) overlay words: the served numbers in words, never a client-side number.
// Fixture values are the D07 oracle's hand values (.scratch/site-scene-generation/d07/plan.md §3).
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import type { ResultQuantity, SceneResults } from '../api/siteSceneResults'
import type { SceneBuilding } from '../api/siteScene'
import {
  featureView, groupsOf, groupView, quantityWords, REASON_WORDS, sceneSummary, snapshotQueryAt, snapshotWords,
} from './sceneResults'

const Q = (value: number | null, unit: string | null, reason: string | null = null): ResultQuantity =>
  ({ value, available: value != null, reason: value == null ? reason : null, unit })
const NA = (reason: string, unit: string | null = null): ResultQuantity => ({ value: null, available: false, reason, unit })

const building = (id: string, i: number): SceneBuilding =>
  ({ id, footprint: [[0, 0], [1, 0], [1, 1]], height_m: 5, source: { namespace: 'manual', source_id: `m${i}` } } as unknown as SceneBuilding)
const names = { buildings: [building('b1', 1), building('b2', 2), building('b3', 3)], groups: [{ id: 'plant', label: 'Ammonia plant', members: ['b1', 'b2'] }] }

const oracle = (): SceneResults => ({
  project: 'p', site_id: 's', scene_revision: 3,
  run: { digest: 'd', current: true, reason: null },
  snapshot: { index: 3, label: '2030-01-01T03:00:00', period: null, weight_generators: Q(1, 'h'), period_years: Q(1, 'a') },
  window: null,
  features: {
    b1: { targets: [{ class: 'Link', uid: 'u-el', name: 'electrolyser', allocation: null, ports: [
      { port: 'p0', bus: 'elec', carrier: 'AC', role: 'input', reversed: false, sign: 'withdrawal_positive', rate: Q(10, 'MW') },
      { port: 'p1', bus: 'h2', carrier: 'H2', role: 'output', reversed: false, sign: 'withdrawal_positive', rate: Q(-5, 'MW') },
    ] }] },
    b2: { targets: [
      { class: 'Link', uid: 'u-hb', name: 'haber', allocation: null, ports: [
        { port: 'p1', bus: 'n2', carrier: 'N2', role: 'input', reversed: false, sign: 'withdrawal_positive', rate: Q(1, 't/h') },
        { port: 'p2', bus: 'nh3', carrier: 'NH3', role: 'output', reversed: false, sign: 'withdrawal_positive', rate: Q(-4, 'MW'), rate_t: NA('conversion_factor_missing', 't/h') },
      ] },
      { class: 'Load', uid: 'u-of', name: 'office', allocation: { basis: 'explicit', weight: 0.4 }, ports: [
        { port: 'p', bus: 'elec', carrier: 'AC', role: 'input', reversed: false, sign: 'withdrawal_positive', rate: Q(0.25, 'MW'), allocated: Q(0.1, 'MW') },
      ] },
      { class: 'Load', uid: 'u-hall', name: 'hall', allocation: { basis: 'shared', features: ['b2', 'b3'] }, ports: [
        { port: 'p', bus: 'elec', carrier: 'AC', role: 'input', reversed: false, sign: 'withdrawal_positive', rate: Q(3, 'MW') },
      ] },
    ] },
    b3: { targets: [
      { class: 'Load', uid: 'u-hall', name: 'hall', allocation: { basis: 'shared', features: ['b2', 'b3'] }, ports: [
        { port: 'p', bus: 'elec', carrier: 'AC', role: 'input', reversed: false, sign: 'withdrawal_positive', rate: Q(3, 'MW') },
      ] },
      { class: 'Generator', uid: 'u-pv', name: 'pv', allocation: null, ports: [
        { port: 'p', bus: 'elec', carrier: 'solar', role: 'output', reversed: false, sign: 'injection_positive', rate: Q(0, 'MW') },
      ] },
      { class: 'StorageUnit', uid: 'u-v', name: 'bess@2030', allocation: null, ports: [], reason: 'vintage_rows_unsupported' },
    ] },
  },
  groups: { plant: {
    boundary: [
      { bus: 'elec', carrier: 'AC', net_import: Q(10.25, 'MW') },
      { bus: 'heat', carrier: 'heat', net_import: Q(-1, 'MW') },
      { bus: 'n2', carrier: 'N2', net_import: Q(1, 't/h') },
      { bus: 'nh3', carrier: 'NH3', net_import: Q(-4, 'MW') },
    ],
    internal_buses: ['h2'],
    totals: [
      { carrier: 'AC', net_import: Q(10.25, 'MW') }, { carrier: 'heat', net_import: Q(-1, 'MW') },
      { carrier: 'N2', net_import: Q(1, 't/h') }, { carrier: 'NH3', net_import: Q(-4, 'MW') },
    ],
    total: NA('mixed_units'),
    unavailable_member: null,
  } },
})

describe('quantities (V57)', () => {
  it('an unavailable number reads its reason in words, never 0.00', () => {
    const w = quantityWords(NA('results_not_current', 'MW'))
    expect(w).toBe('Unavailable: the results are out of date because the model changed after the solve.')
    expect(w).not.toMatch(/\d/)
    expect(quantityWords({ value: 0, available: false, reason: 'series_missing', unit: 'MW' })).toMatch(/^Unavailable: /)
  })
  it('a genuine zero is 0 in its unit; a material rate stays t/h', () => {
    expect(quantityWords(Q(0, 'MW'))).toBe('0 MW')
    expect(quantityWords(Q(-0, 'MW'))).toBe('0 MW')
    expect(quantityWords(Q(1, 't/h'))).toBe('1 t/h')
    expect(quantityWords(Q(13.25, 'MW'))).toBe('13.25 MW')
  })
  it('every reason the route can give has words (parity with results.REASONS)', () => {
    const py = readFileSync(join(__dirname, '..', '..', '..', 'backend', 'services', 'site_scene', 'results.py'), 'utf8')
    const block = /REASONS: tuple\[str, \.\.\.\] = \(([\s\S]*?)\)\n/.exec(py)![1]
    const codes = [...block.matchAll(/"([a-z_]+)"/g)].map(m => m[1])
    expect(codes.length).toBeGreaterThan(10)
    expect(Object.keys(REASON_WORDS).sort()).toEqual([...codes].sort())
  })
})

describe('the timeline index as native labels (V87)', () => {
  it('hourly, quarter-hourly and representative labels are passed through, never derived', () => {
    const q15 = { snapshots: ['2030-01-01T00:00:00', '2030-01-01T00:15:00', '2030-01-01T00:30:00'] }
    expect(snapshotQueryAt(q15, 2)).toEqual({ snapshot: '2030-01-01T00:30:00', period: null })
    const rep = { snapshots: ['2030-01-06T00:00:00', '2030-04-14T12:00:00'] }
    expect(snapshotQueryAt(rep, 1)).toEqual({ snapshot: '2030-04-14T12:00:00', period: null })
  })
  it('multi-period: the period of the index goes along; a numbered model passes its integer label', () => {
    const mp = { snapshots: ['2030-01-01T00:00:00', '2030-01-01T00:00:00'], periods: [2030, 2040] }
    expect(snapshotQueryAt(mp, 1)).toEqual({ snapshot: '2030-01-01T00:00:00', period: '2040' })
    expect(snapshotQueryAt({ snapshots: ['0', '1', '2'] }, 1)).toEqual({ snapshot: '1', period: null })
  })
  it('an index outside the model is no snapshot (never clamped)', () => {
    expect(snapshotQueryAt({ snapshots: ['a'] }, 1)).toBeNull()
    expect(snapshotQueryAt({ snapshots: ['a'] }, -1)).toBeNull()
    expect(snapshotQueryAt(undefined, 0)).toBeNull()
  })
  it('the heading shows the served label, period and weight, not a computed step', () => {
    const r = oracle()
    expect(snapshotWords(r)).toBe('2030-01-01 03:00:00 · weight 1 h')
    r.snapshot = { ...r.snapshot!, label: 7, period: 2040, weight_generators: Q(0.25, 'h') }
    expect(snapshotWords(r)).toBe('Snapshot 7 · period 2040 · weight 0.25 h')
  })
})

describe('a building’s items', () => {
  it('ports read the served rates in their native units', () => {
    const v = featureView(oracle(), 'b2', names, 'expert')
    const haber = v.find(t => t.title === 'haber · Link')!
    expect(haber.ports.map(p => p.value)).toEqual(['1 t/h', '-4 MW'])
    expect(haber.ports[1].notes).toEqual([])              // an unavailable tonne equivalent is not shown as a number
  })
  it('an explicit share is labelled allocated, with the native rate beside it', () => {
    const office = featureView(oracle(), 'b2', names, 'guided').find(t => t.title === 'office · load')!
    expect(office.allocation).toBe('allocated 40 %')
    expect(office.ports[0]).toMatchObject({ label: 'AC in', value: '0.1 MW', tag: 'allocated', notes: ['of 0.25 MW in total'] })
  })
  it('a shared target names the other building and is not split', () => {
    const hall = featureView(oracle(), 'b2', names, 'guided').find(t => t.title === 'hall · load')!
    expect(hall.allocation).toBe('shared with Building 3 · not split')
    expect(hall.ports[0]).toMatchObject({ value: '3 MW', tag: null })
  })
  it('a vintaged target reads its reason; a zero output reads 0', () => {
    const v = featureView(oracle(), 'b3', names, 'expert')
    expect(v.find(t => t.title.startsWith('bess'))!.reason).toBe('Unavailable: items with several build years are not shown per hour yet.')
    expect(v.find(t => t.title === 'pv · Generator')!.ports[0].value).toBe('0 MW')
  })
  it('the scene summary shows a shared target once, with every building it sits on', () => {
    const rows = sceneSummary(oracle(), names, 'guided')
    const hall = rows.filter(r => r.view.title === 'hall · load')
    expect(hall).toHaveLength(1)
    expect(hall[0].on).toEqual(['Building 2', 'Building 3'])
    expect(rows.filter(r => r.view.title === 'electrolyser · process unit')).toHaveLength(1)
  })
})

describe('a group', () => {
  it('shows boundary rows and per-carrier totals as served; no cross-unit total', () => {
    const g = groupView(oracle().groups.plant, 'expert')
    expect(g.boundary.map(b => `${b.label}: ${b.value}`)).toEqual([
      'elec · AC: 10.25 MW', 'heat · heat: -1 MW', 'n2 · N2: 1 t/h', 'nh3 · NH3: -4 MW'])
    expect(g.totals.map(t => t.value)).toEqual(['10.25 MW', '-1 MW', '1 t/h', '-4 MW'])
    expect(g.total).toBe('Unavailable: there is no single total because the carriers have different units.')
    expect(g.notes).toEqual(['Inside the group and not counted: h2'])
  })
  // The server's shape (results._unavailable_group): no rows, the reason on `total`, the member's label.
  const unavailableGroup = (reason: string, member: string) =>
    ({ boundary: [], internal_buses: [], totals: [], total: NA(reason), unavailable_member: member })
  it('a partially allocated member is named with that reason', () => {
    const v = groupView(unavailableGroup('group_partial_allocation', 'Load:office'), 'guided')
    expect(v.total).toBe('Unavailable: a member is shared with buildings outside the group.')
    expect(v.notes).toEqual(['Unavailable: a member is shared with buildings outside the group (office · load).'])
  })
  it('an unavailable member is named with that reason', () => {
    const v = groupView(unavailableGroup('member_unavailable', 'Link:haber'), 'expert')
    expect(v.total).toBe('Unavailable: a member’s result is unavailable.')
    expect(v.notes).toEqual(['Unavailable: a member’s result is unavailable (haber · Link).'])
    expect(v.boundary).toEqual([]); expect(v.totals).toEqual([])
  })
  it('lists the groups a building sits in, nested ones too', () => {
    const groups = [...names.groups, { id: 'site', label: 'Site', members: ['plant'] }, { id: 'other', label: 'Other', members: ['b3'] }]
    expect(groupsOf('b1', groups).map(g => g.id)).toEqual(['plant', 'site'])
    expect(groupsOf('b3', groups).map(g => g.id)).toEqual(['other'])
  })
})

describe('a closed run gate', () => {
  it('says the run reason once; leaves with that reason read plain Unavailable, others keep theirs', () => {
    const r = oracle()
    r.run = { digest: 'd', current: false, reason: 'results_not_current' }
    r.features.b1.targets[0].ports[0].rate = NA('results_not_current', 'MW')
    r.features.b2.targets[1].ports[0] = { ...r.features.b2.targets[1].ports[0], rate: NA('results_not_current', 'MW'), allocated: NA('results_not_current', 'MW') }
    expect(featureView(r, 'b1', names, 'expert')[0].ports[0].value).toBe('Unavailable')
    const office = featureView(r, 'b2', names, 'expert').find(t => t.title === 'office · Load')!
    expect(office.ports[0]).toMatchObject({ value: 'Unavailable', tag: 'allocated', notes: [] })
    expect(quantityWords(NA('unit_unknown'), 'results_not_current')).toBe('Unavailable: the bus unit is not one the overlay knows (MW or t).')
    expect(groupView({ ...r.groups.plant, total: NA('results_not_current') }, 'expert', 'results_not_current').total).toBe('Unavailable')
  })
})

describe('Guided directions (no double negative)', () => {
  it('a port says in or out from the served sign and shows the number without a minus', () => {
    const haber = featureView(oracle(), 'b2', names, 'guided').find(t => t.title === 'haber · process unit')!
    expect(haber.ports.map(p => `${p.label}: ${p.value}`)).toEqual(['N2 in: 1 t/h', 'NH3 out: 4 MW'])
    const pv = featureView(oracle(), 'b3', names, 'guided').find(t => t.title === 'pv · generator')!
    expect(`${pv.ports[0].label}: ${pv.ports[0].value}`).toBe('solar out: 0 MW')   // zero: the role word
    const inj = { ...oracle().features.b3.targets[1].ports[0], rate: Q(2, 'MW') }
    const r = oracle(); r.features.b3.targets[1].ports[0] = inj
    expect(featureView(r, 'b3', names, 'guided').find(t => t.title === 'pv · generator')!.ports[0].label).toBe('solar out')
    r.features.b3.targets[1].ports[0] = { ...inj, rate: Q(-2, 'MW') }
    expect(featureView(r, 'b3', names, 'guided').find(t => t.title === 'pv · generator')!.ports[0]).toMatchObject({ label: 'solar in', value: '2 MW' })
  })
  it('Expert keeps the signed value', () => {
    const haber = featureView(oracle(), 'b2', names, 'expert').find(t => t.title === 'haber · Link')!
    expect(haber.ports[1]).toMatchObject({ label: 'p2 · nh3 · out', value: '-4 MW' })
  })
  it('a group says import or export per row and total', () => {
    const g = groupView(oracle().groups.plant, 'guided')
    expect(g.boundary.map(b => `${b.label}: ${b.value}`)).toEqual(['AC import: 10.25 MW', 'heat export: 1 MW', 'N2 import: 1 t/h', 'NH3 export: 4 MW'])
    expect(g.totals.map(b => `${b.label}: ${b.value}`)).toEqual(['AC import: 10.25 MW', 'heat export: 1 MW', 'N2 import: 1 t/h', 'NH3 export: 4 MW'])
    const z = groupView({ ...oracle().groups.plant, totals: [{ carrier: 'AC', net_import: Q(0, 'MW') }, { carrier: 'N2', net_import: NA('series_missing', 't/h') }] }, 'guided')
    expect(z.totals.map(b => `${b.label}: ${b.value}`)).toEqual(['AC net import: 0 MW', 'N2 net import: Unavailable: the solve wrote no result for this item.'])
  })
})
