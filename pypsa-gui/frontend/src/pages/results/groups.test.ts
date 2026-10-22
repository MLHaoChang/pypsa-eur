import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { CARD_GROUPS, RESULT_GROUPS, cardPlaceOf, groupOf, type ResultsTab } from './groups'

const ALL_TABS: ResultsTab[] = [
  'summary', 'overview', 'capex', 'dispatch', 'loadflow', 'prices', 'emissions',
  'economics', 'curtailment', 'lostload', 'adequacy', 'storage', 'asset', 'fmea', 'investment', 'flows', 'loadshape',
  'grid_code', 'electrical_design',
]

describe('Results groups (UX overhaul S3, owner decisions D1a / D2a)', () => {
  it('orders the groups Summary, Investment, Grid plan, Reliability, Operation', () => {
    expect(RESULT_GROUPS.map(g => g.label)).toEqual(
      ['Summary', 'Investment', 'Grid plan', 'Reliability', 'Operation'])
  })

  it('puts every tab but Asset detail in exactly one group', () => {
    const grouped = RESULT_GROUPS.flatMap(g => g.tabs)
    expect(new Set(grouped).size).toBe(grouped.length)
    expect(new Set(grouped)).toEqual(new Set(ALL_TABS.filter(t => t !== 'asset')))
    expect(groupOf('asset')).toBeNull()
  })

  it.each([
    ['capex', 'investment'], ['economics', 'investment'], ['prices', 'investment'], ['investment', 'investment'],
    ['loadflow', 'grid_plan'], ['grid_code', 'grid_plan'], ['electrical_design', 'grid_plan'],
    ['adequacy', 'reliability'], ['lostload', 'reliability'], ['fmea', 'reliability'],
    ['dispatch', 'operation'], ['flows', 'operation'], ['loadshape', 'operation'], ['storage', 'operation'], ['curtailment', 'operation'], ['emissions', 'operation'],
    ['summary', 'summary'], ['overview', 'summary'],
  ] as const)('files %s under %s', (tab, group) => {
    expect(groupOf(tab)).toBe(group)
  })
})

// W10 R1: the card groups mount today's tabs under the card and card tab the
// backend registry (services/result_views.py) names. A tab filed differently
// on the two sides would open under the wrong card.
describe('Results card groups (W10 R1)', () => {
  it('lists Summary and the six cards, in flow_scope order', () => {
    expect(CARD_GROUPS.map(g => g.id)).toEqual(
      ['summary', 'capacity', 'grid', 'control', 'reliability', 'commercial', 'handoff'])
  })

  it('files every tab but Asset Detail exactly once; at most four card tabs per card', () => {
    const filed = CARD_GROUPS.flatMap(g => g.tabs.flatMap(t => t.views))
    expect(new Set(filed).size).toBe(filed.length)
    expect(new Set(filed)).toEqual(new Set(ALL_TABS.filter(t => t !== 'asset')))
    expect(cardPlaceOf('asset')).toBeNull()
    for (const g of CARD_GROUPS) expect(g.tabs.length).toBeLessThanOrEqual(4)
  })

  it('matches the card and card_tab of services/result_views.py', () => {
    const src = readFileSync(resolve(__dirname, '../../../../backend/services/result_views.py'), 'utf8')
    const re = /ResultView\("(\w+)"[\s\S]*?(?=\n    ResultView\(|\n\)\n)/g
    let seen = 0
    for (const m of src.matchAll(re)) {
      const card = /card="(\w+)"/.exec(m[0])?.[1]
      const cardTab = /card_tab="(\w+)"/.exec(m[0])?.[1]
      const place = cardPlaceOf(m[1] as ResultsTab)
      if (card === undefined) { expect(place?.group ?? null).toBe(m[1] === 'summary' ? 'summary' : null); continue }
      expect({ id: m[1], ...place }).toEqual({ id: m[1], group: card, cardTab })
      seen++
    }
    expect(seen).toBe(17)
  })
})
