import { render, screen } from '@testing-library/react'
import { beforeAll, describe, expect, it } from 'vitest'

// jsdom reports 0 for every measured box; give the virtualiser a real viewport.
// Must run before any render() call below — see AssetPicker.test.tsx for the
// same pattern. `beforeAll` is imported explicitly because this project runs
// `globals: false`.
beforeAll(() => {
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight',
    { configurable: true, value: 600 })
  Object.defineProperty(HTMLElement.prototype, 'getBoundingClientRect',
    { configurable: true, value: () => ({ height: 600, width: 600, top: 0, left: 0,
      right: 600, bottom: 600, x: 0, y: 0, toJSON: () => ({}) }) })
})

import AssetTable, { tableRows } from './AssetTable'
import type { AssetResultsResponse } from './types'

const base = (over: Partial<AssetResultsResponse>): AssetResultsResponse => ({
  asset: { class: 'Generator', name: 'Gas 1', carrier: 'gas', bus: 'B1', params: {} },
  solve: { source: 'lopf', objective: 1, solve_time: 1, condition: 'optimal' },
  category: 'dispatch', mode: 'chronological', categories: [], metrics: [],
  scalars: {}, headline: [], index: [], periods: null, pct_of_hours: null,
  columns: [], series: {},
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

  // Monthly buckets are keyed `period|month` server-side, so a three-period
  // network yields 36 rows carrying only 12 distinct month labels. Without the
  // period column those rows are indistinguishable.
  it('adds the period column to monthly buckets too, not just chronological', () => {
    const { header, rows } = tableRows(base({
      mode: 'monthly', index: ['2026-01', '2026-01'], periods: [2027, 2028],
      columns: [{ id: 'p__mean', label: 'p', unit: 'MW', metric_id: 'p', agg: 'mean' }],
      series: { p__mean: [58, 61] },
    }))
    expect(header).toEqual(['month', 'period', 'p (MW)'])
    expect(rows).toEqual([['2026-01', 2027, 58], ['2026-01', 2028, 61]])
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

describe('AssetTable (rendering)', () => {
  it('shows the empty-state prompt in chronological mode with nothing ticked', () => {
    // periods present -> header would be ['snapshot', 'period'] (length 2) if
    // the empty state were still keyed off `header.length <= 1` — this is the
    // case that guard would miss, since it only ever fires at length 1.
    render(<AssetTable data={base({
      index: ['a'], periods: [2026], columns: [], series: {},
    })} />)
    expect(screen.getByText(/no time series selected/i)).toBeTruthy()
  })

  it('shows the empty-state prompt in duration mode with nothing ticked', () => {
    // header would be ['rank', 'pct_of_hours'] (length 2) with zero metric
    // columns — a `header.length <= 1` guard would render an empty-metric
    // table here instead of the prompt.
    render(<AssetTable data={base({
      mode: 'duration', index: ['1', '2'], pct_of_hours: [0.5, 1],
      columns: [], series: {},
    })} />)
    expect(screen.getByText(/no time series selected/i)).toBeTruthy()
  })

  it('shows the empty-state prompt in monthly mode with nothing ticked', () => {
    render(<AssetTable data={base({
      mode: 'monthly', index: ['2026-01'], columns: [], series: {},
    })} />)
    expect(screen.getByText(/no time series selected/i)).toBeTruthy()
  })

  it('formats every number to two decimals, with null and non-finite blank', () => {
    render(<AssetTable data={base({
      index: ['a', 'b', 'c', 'd', 'e'],
      columns: [{ id: 'p', label: 'p', unit: 'MW', metric_id: 'p', agg: null }],
      series: { p: [120, 58.4321, null, NaN, 12345.6] },
    })} />)
    const cells = screen.getAllByRole('cell')
    // Cells alternate [snapshot, p] per row. Integers get their decimals too
    // — a column where some rows read "120" and others "58.43" does not line
    // up on the decimal point, which is the whole point of tabular-nums.
    expect(cells.map(c => c.textContent)).toEqual([
      'a', '120.00',
      'b', '58.43',
      'c', '',
      'd', '',
      'e', '12,345.60',
    ])
  })

  it('renders a real but tiny value in exponential rather than as 0.00', () => {
    // A capacity factor of 0.0008 pu and a genuine zero are different
    // results; rounding both to "0.00" would hide a barely-running asset
    // behind an idle one.
    render(<AssetTable data={base({
      index: ['a', 'b'],
      columns: [{ id: 'cf', label: 'cf', unit: 'pu', metric_id: 'cf', agg: null }],
      series: { cf: [0.0008, 0] },
    })} />)
    const cells = screen.getAllByRole('cell')
    expect(cells[1].textContent).toBe('8.00e-4')
    expect(cells[3].textContent).toBe('0.00')
  })

  it('renders the same number of cells per row as there are header columns', () => {
    const data = base({
      index: ['a', 'b'], periods: [2026, 2027],
      columns: [
        { id: 'p', label: 'p', unit: 'MW', metric_id: 'p', agg: null },
        { id: 'q', label: 'q', unit: 'MW', metric_id: 'q', agg: null },
      ],
      series: { p: [1, 2], q: [3, 4] },
    })
    render(<AssetTable data={data} />)
    const headers = screen.getAllByRole('columnheader')
    const cells = screen.getAllByRole('cell')
    expect(headers).toHaveLength(4)   // snapshot, period, p (MW), q (MW)
    expect(cells).toHaveLength(headers.length * 2)   // 2 data rows
  })
})
