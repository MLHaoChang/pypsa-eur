import { describe, expect, it } from 'vitest'
import type { EnergyFlowRow } from '../../api/energyFlows'
import { CSV_HEADER, csvRows, fmtMwh, sankeyData, share } from './energyFlows'

export const ROWS: EnergyFlowRow[] = [
  { source: 'solar', via: 'Direct', destination: 'load', mwh: 600 },
  { source: 'solar', via: 'Battery', destination: 'load', mwh: 100 },
  { source: 'Grid import', via: 'Battery', destination: 'load', mwh: 50 },
  { source: 'Grid import', via: 'Direct', destination: 'load', mwh: 250 },
  { source: 'Grid import', via: 'Direct', destination: 'Losses', mwh: 20 },
  { source: 'solar', via: 'Battery', destination: 'Losses', mwh: 10 },
]

describe('sankeyData', () => {
  const g = sankeyData(ROWS)
  const name = (i: number) => `${g.nodes[i].column}:${g.nodes[i].name}`
  const link = (a: string, b: string) => g.links.find(l => name(l.source) === a && name(l.target) === b)

  it('has one node per source, via and destination', () => {
    expect(g.nodes.map(n => `${n.column}:${n.name}`).sort()).toEqual([
      'destination:Losses', 'destination:load', 'source:Grid import', 'source:solar',
      'via:Battery', 'via:Direct'])
  })

  it('keeps "solar via Battery" and "grid via Battery" as separate links into the battery', () => {
    expect(link('source:solar', 'via:Battery')!.value).toBe(110)
    expect(link('source:Grid import', 'via:Battery')!.value).toBe(50)
  })

  it('conserves each via node: what goes in comes out', () => {
    for (const via of ['Battery', 'Direct']) {
      const into = g.links.filter(l => name(l.target) === `via:${via}`).reduce((s, l) => s + l.value, 0)
      const out = g.links.filter(l => name(l.source) === `via:${via}`).reduce((s, l) => s + l.value, 0)
      expect(into).toBeCloseTo(out, 9)
    }
  })

  it('lists the source/via/destination rows behind a link, largest first', () => {
    const l = link('via:Direct', 'destination:load')!
    expect(l.rows.map(r => `${r.source}|${r.mwh}`)).toEqual(['solar|600', 'Grid import|250'])
  })

  it('colours each link by the source that supplies most of it', () => {
    const color = (src: string) => g.nodes.find(n => n.column === 'source' && n.name === src)!.color
    expect(link('via:Direct', 'destination:load')!.color).toBe(color('solar'))      // 600 solar vs 250 grid
    expect(link('source:Grid import', 'via:Battery')!.color).toBe(color('Grid import'))
    expect(link('via:Battery', 'destination:load')!.color).toBe(color('solar'))     // 100 vs 50
  })

  it('draws nothing for a zero-energy period', () => {
    expect(sankeyData([])).toEqual({ nodes: [], links: [] })
  })
})

describe('csvRows', () => {
  it('carries the chart rows, header first column Source', () => {
    expect(CSV_HEADER).toEqual(['Source', 'Via', 'Destination', 'MWh'])
    expect(csvRows(ROWS)).toHaveLength(ROWS.length)
    expect(csvRows(ROWS)[1]).toEqual(['solar', 'Battery', 'load', 100])
    // the CSV total is the chart's total
    expect(csvRows(ROWS).reduce((s, r) => s + (r[3] as number), 0))
      .toBe(sankeyData(ROWS).links.filter(l => l.source < 2).reduce((s, l) => s + l.value, 0))
  })
})

describe('formatting', () => {
  it('prefixes units and never prints a missing value as zero', () => {
    expect(fmtMwh(12.34)).toBe('12.3 MWh')
    expect(fmtMwh(12_340)).toMatch(/GWh$/)
    expect(fmtMwh(null)).toBe('not established')
    expect(share(1, 0)).toBeNull()
    expect(share(1, 4)).toBe(0.25)
  })
})
