import { describe, expect, it } from 'vitest'
import { tableRows } from './AssetTable'
import type { AssetResultsResponse } from './types'

const base = (over: Partial<AssetResultsResponse>): AssetResultsResponse => ({
  asset: { class: 'Generator', name: 'Gas 1', carrier: 'gas', bus: 'B1', params: {} },
  solve: { source: 'lopf', objective: 1, solve_time: 1, condition: 'optimal' },
  category: 'dispatch', mode: 'chronological', categories: [], metrics: [],
  scalars: {}, index: [], periods: null, pct_of_hours: null, columns: [], series: {},
  ...over,
})

describe('tableRows', () => {
  it('puts snapshot first in chronological mode', () => {
    const { header, rows } = tableRows(base({
      index: ['2026-01-01T00:00:00', '2026-01-01T01:00:00'],
      columns: [{ id: 'p', label: 'Active power', unit: 'MW', metric_id: 'p', agg: null }],
      series: { p: [120, 135] },
    }))
    expect(header).toEqual(['snapshot', 'Active power (MW)'])
    expect(rows).toEqual([
      ['2026-01-01T00:00:00', 120], ['2026-01-01T01:00:00', 135],
    ])
  })

  it('adds a period column only when the response carries periods', () => {
    const { header } = tableRows(base({
      index: ['a'], periods: [2026],
      columns: [{ id: 'p', label: 'p', unit: 'MW', metric_id: 'p', agg: null }],
      series: { p: [1] },
    }))
    expect(header).toEqual(['snapshot', 'period', 'p (MW)'])
  })

  it('uses rank and pct_of_hours in duration mode', () => {
    const { header, rows } = tableRows(base({
      mode: 'duration', index: ['1', '2'], pct_of_hours: [0.5, 1],
      columns: [{ id: 'p', label: 'p', unit: 'MW', metric_id: 'p', agg: null }],
      series: { p: [135, 120] },
    }))
    expect(header).toEqual(['rank', 'pct_of_hours', 'p (MW)'])
    expect(rows[0]).toEqual(['1', 0.5, 135])
  })

  it('uses month in monthly mode and keeps the aggregated column labels', () => {
    const { header } = tableRows(base({
      mode: 'monthly', index: ['2026-01'],
      columns: [
        { id: 'p__mean', label: 'Active power (mean)', unit: 'MW', metric_id: 'p', agg: 'mean' },
        { id: 'p__energy', label: 'Active power (energy)', unit: 'MWh', metric_id: 'p', agg: 'energy' },
      ],
      series: { p__mean: [58], p__energy: [512] },
    }))
    expect(header).toEqual(['month', 'Active power (mean) (MW)', 'Active power (energy) (MWh)'])
  })

  it('renders a missing value as an empty cell rather than NaN', () => {
    const { rows } = tableRows(base({
      index: ['a'],
      columns: [{ id: 'p', label: 'p', unit: 'MW', metric_id: 'p', agg: null }],
      series: { p: [null] },
    }))
    expect(rows[0][1]).toBeNull()
  })
})
