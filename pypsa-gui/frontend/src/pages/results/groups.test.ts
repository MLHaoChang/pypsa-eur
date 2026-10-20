import { describe, expect, it } from 'vitest'
import { RESULT_GROUPS, groupOf, type ResultsTab } from './groups'

const ALL_TABS: ResultsTab[] = [
  'summary', 'overview', 'capex', 'dispatch', 'loadflow', 'prices', 'emissions',
  'economics', 'curtailment', 'lostload', 'adequacy', 'storage', 'asset', 'fmea', 'investment', 'flows',
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
    ['loadflow', 'grid_plan'],
    ['adequacy', 'reliability'], ['lostload', 'reliability'], ['fmea', 'reliability'],
    ['dispatch', 'operation'], ['flows', 'operation'], ['storage', 'operation'], ['curtailment', 'operation'], ['emissions', 'operation'],
    ['summary', 'summary'], ['overview', 'summary'],
  ] as const)('files %s under %s', (tab, group) => {
    expect(groupOf(tab)).toBe(group)
  })
})
