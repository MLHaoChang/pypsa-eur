import { describe, expect, it } from 'vitest'
import { groupColumnsByUnit } from './AssetCharts'
import type { ColumnSpec } from './types'

const c = (id: string, unit: string): ColumnSpec =>
  ({ id, label: id, unit, metric_id: id, agg: null })

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
