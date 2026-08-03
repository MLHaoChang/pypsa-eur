import { describe, expect, it } from 'vitest'
import { chartRows, groupColumnsByUnit } from './AssetCharts'
import type { AssetResultsResponse, ColumnSpec } from './types'

const c = (id: string, unit: string): ColumnSpec =>
  ({ id, label: id, unit, metric_id: id, agg: null })

const base = (over: Partial<AssetResultsResponse>): AssetResultsResponse => ({
  asset: { class: 'Generator', name: 'PV', carrier: 'solar', bus: 'B3', params: {} },
  solve: { source: 'lopf', objective: 1, solve_time: 1, condition: 'optimal' },
  category: 'dispatch', mode: 'chronological', categories: [], metrics: [],
  scalars: {}, headline: [], index: [], periods: null, pct_of_hours: null,
  columns: [], series: {},
  ...over,
})

/** One calendar day, replayed under each investment period — the real shape. */
const threePeriodDay = (hours: number[]) => base({
  index: [2027, 2028, 2029].flatMap(() =>
    hours.map(h => `2026-06-15T${String(h).padStart(2, '0')}:00:00`)),
  periods: [2027, 2028, 2029].flatMap(p => hours.map(() => p)),
  columns: [c('available', 'MW')],
  series: { available: [2027, 2028, 2029].flatMap(() => hours.map(() => 0)) },
})

describe('groupColumnsByUnit', () => {
  it('keeps one group when every series shares a unit', () => {
    const g = groupColumnsByUnit([c('p', 'MW'), c('curtailment', 'MW')])
    expect(g).toHaveLength(1)
    expect(g[0].unit).toBe('MW')
    expect(g[0].columns.map(x => x.id)).toEqual(['p', 'curtailment'])
  })

  it('splits into one group per distinct unit', () => {
    const g = groupColumnsByUnit([c('p', 'MW'), c('mu', 'EUR/MWh'), c('cf', 'pu')])
    expect(g.map(x => x.unit)).toEqual(['MW', 'EUR/MWh', 'pu'])
  })

  it('preserves first-seen unit order so the layout is stable across renders', () => {
    const g = groupColumnsByUnit([c('mu', 'EUR/MWh'), c('p', 'MW'), c('mu2', 'EUR/MWh')])
    expect(g.map(x => x.unit)).toEqual(['EUR/MWh', 'MW'])
    expect(g[0].columns.map(x => x.id)).toEqual(['mu', 'mu2'])
  })

  it('groups unitless series together under a dash', () => {
    const g = groupColumnsByUnit([c('status', ''), c('start_up', '')])
    expect(g).toHaveLength(1)
    expect(g[0].unit).toBe('–')
  })

  it('returns nothing for no columns', () => {
    expect(groupColumnsByUnit([])).toEqual([])
  })
})

describe('chartRows', () => {
  it('plots the bare stamp when the network has no investment periods', () => {
    const { xKey, rows } = chartRows(base({
      index: ['2026-01-01T00:00:00', '2026-01-01T01:00:00'],
      columns: [c('p', 'MW')], series: { p: [120, 135] },
    }))
    expect(xKey).toBe('snapshot')
    expect(rows).toEqual([
      { snapshot: '2026-01-01T00:00:00', p: 120 },
      { snapshot: '2026-01-01T01:00:00', p: 135 },
    ])
  })

  // The reported bug: one day of a three-period network drew the same day
  // three times, because every period contributed the identical stamp.
  it('gives each period its own X value instead of three identical days', () => {
    const { rows } = chartRows(threePeriodDay([0, 1, 2]))
    expect(rows).toHaveLength(9)
    const xs = rows.map(r => r.snapshot)
    expect(new Set(xs).size).toBe(9)
    expect(xs[0]).toBe('2027 · 06-15 00:00')
    expect(xs[3]).toBe('2028 · 06-15 00:00')
    expect(xs[6]).toBe('2029 · 06-15 00:00')
  })

  it('leaves the axis unprefixed when a single period is selected', () => {
    const { rows } = chartRows(base({
      index: ['2026-06-15T00:00:00', '2026-06-15T01:00:00'],
      periods: [2027, 2027],
      columns: [c('p', 'MW')], series: { p: [1, 2] },
    }))
    expect(rows.map(r => r.snapshot))
      .toEqual(['2026-06-15T00:00:00', '2026-06-15T01:00:00'])
  })

  it('qualifies monthly buckets without slicing the month away', () => {
    const { xKey, rows } = chartRows(base({
      mode: 'monthly', index: ['2026-01', '2026-01'], periods: [2027, 2028],
      columns: [c('p__mean', 'MW')], series: { p__mean: [58, 61] },
    }))
    expect(xKey).toBe('month')
    expect(rows.map(r => r.month)).toEqual(['2027 · 2026-01', '2028 · 2026-01'])
  })

  it('leaves duration ranks alone — a rank is not a moment in time', () => {
    const { xKey, rows } = chartRows(base({
      mode: 'duration', index: ['1', '2'], pct_of_hours: [0.5, 1],
      columns: [c('p', 'MW')], series: { p: [135, 120] },
    }))
    expect(xKey).toBe('rank')
    expect(rows).toEqual([{ rank: '1', p: 135 }, { rank: '2', p: 120 }])
  })

  it('carries a missing sample through as null rather than dropping the row', () => {
    const { rows } = chartRows(base({
      index: ['a', 'b'], columns: [c('p', 'MW')], series: { p: [1] },
    }))
    expect(rows).toHaveLength(2)
    expect(rows[1].p).toBeNull()
  })
})
