import { describe, expect, it } from 'vitest'
import { describeWay, suggestionCandidates, SUGGEST_MAX_END_GAP_M, type SuggestBranch } from './osmSuggest'

const A: [number, number] = [53.0, 6.0]
const B: [number, number] = [53.0, 7.0]
const C: [number, number] = [54.0, 6.0]
const branches: SuggestBranch[] = [
  { key: 'line:L1', label: 'L1', bus0: A, bus1: B },
  { key: 'link:K1', label: 'K1', bus0: B, bus1: A },
  { key: 'tr:T1', label: 'T1', bus0: A, bus1: C },
]
// ~110 m north of A and of B
const nearA: [number, number] = [53.001, 6.0]
const nearB: [number, number] = [53.001, 7.0]

describe('suggestionCandidates', () => {
  it('offers every branch whose buses sit at the way\'s ends, oriented bus0→bus1', () => {
    const c = suggestionCandidates([nearB, [53.5, 6.5], nearA], branches)
    expect(c.map(x => x.key)).toEqual(['line:L1', 'link:K1'])
    expect(c[0].waypoints).toEqual([nearA, [53.5, 6.5], nearB])   // reversed for L1 (A→B)
    expect(c[1].waypoints).toEqual([nearB, [53.5, 6.5], nearA])   // as drawn for K1 (B→A)
    expect(c[0].gapM).toBeGreaterThan(200)
    expect(c[0].gapM).toBeLessThan(250)
  })

  it('refuses a way whose end is beyond the gap, and a way of one vertex', () => {
    const far: [number, number] = [53.01, 7.0]   // ~1.1 km from B
    expect(suggestionCandidates([nearA, far], branches)).toEqual([])
    expect(suggestionCandidates([nearA, far], branches, 2000).map(x => x.key)).toEqual(['line:L1', 'link:K1'])
    expect(suggestionCandidates([nearA], branches)).toEqual([])
    expect(SUGGEST_MAX_END_GAP_M).toBe(500)
  })

  it('sorts nearest first', () => {
    const exact = suggestionCandidates([A, [53.5, 6.5], B], [branches[1], branches[0]])
    expect(exact[0].gapM).toBe(0)
    const c = suggestionCandidates([nearA, nearB], [{ key: 'x', label: 'x', bus0: [53.002, 6.0], bus1: B }, branches[0]])
    expect(c.map(x => x.key)).toEqual(['line:L1', 'x'])
  })
})

describe('describeWay', () => {
  it('reads voltage and name from the tags', () => {
    expect(describeWay('line', { voltage: '110000', name: 'Noordring' })).toBe('110 kV line (Noordring)')
    expect(describeWay('minor_line', {})).toBe('minor line')
    expect(describeWay('cable', { voltage: '400;110' })).toBe('400 V / 110 V cable')
    expect(describeWay('line', { voltage: 'abc' })).toBe('abc line')
  })
})
