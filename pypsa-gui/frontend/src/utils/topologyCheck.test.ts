// The five topology rules (plan M5), each on a hand-made network, and the
// thresholds they are written against.
import { describe, expect, it } from 'vitest'
import { NEAR_BUSES_M, TRANSFORMER_SPAN_M, topologyCheck, type TopologyInput } from './topologyCheck'
import { LENGTH_DISCREPANCY_ABS_KM, LENGTH_DISCREPANCY_REL, routeLengthKm } from './geo'

const A = { name: 'A', x: 6.0, y: 53.0 }
const B = { name: 'B', x: 7.0, y: 53.0 }
const base = (over: Partial<TopologyInput> = {}): TopologyInput => ({
  buses: [A, B], lines: [], links: [], transformers: [], routes: {}, sites: [], ...over,
})
const kinds = (r: TopologyInput) => topologyCheck(r).map(f => f.kind)

describe('thresholds', () => {
  it('are the documented constants', () => {
    expect(NEAR_BUSES_M).toBe(5)
    expect(TRANSFORMER_SPAN_M).toBe(500)
    expect(LENGTH_DISCREPANCY_REL).toBe(0.1)
    expect(LENGTH_DISCREPANCY_ABS_KM).toBe(0.1)
  })
})

describe('unplaced buses', () => {
  it('lists each bus without a usable location, as info, deep-linked to the bus', () => {
    const f = topologyCheck(base({ buses: [A, { name: 'U', x: 0, y: 0 }, { name: 'N', x: null, y: 53 }] }))
    expect(f.map(x => [x.kind, x.severity, x.component])).toEqual([
      ['unplaced', 'info', { type: 'Bus', name: 'U' }],
      ['unplaced', 'info', { type: 'Bus', name: 'N' }],
    ])
    expect(f[0].message).toContain('U has no location')
  })
})

describe('two buses within 5 m', () => {
  it('flags a pair closer than the threshold once, on the first bus, naming the other', () => {
    const twin = { name: 'A2', x: 6.0, y: 53.00003 }      // ~3.3 m north of A
    const f = topologyCheck(base({ buses: [A, twin, B] }))
    expect(f).toHaveLength(1)
    expect(f[0]).toMatchObject({ kind: 'nearBuses', severity: 'warn', component: { type: 'Bus', name: 'A' }, other: 'A2' })
    expect(f[0].message).toMatch(/3\.3 m apart/)
  })
  it('does not flag buses 6 m apart', () => {
    expect(kinds(base({ buses: [A, { name: 'A2', x: 6.0, y: 53.000055 }] }))).toEqual([])
  })
})

describe('transformer span', () => {
  const far = { name: 'F', x: 6.01, y: 53.0 }   // ~660 m east of A
  const near = { name: 'N', x: 6.005, y: 53.0 } // ~330 m
  it('flags a transformer whose buses are more than 500 m apart', () => {
    const f = topologyCheck(base({ buses: [A, far, near], transformers: [{ name: 'T1', bus0: 'A', bus1: 'F' }, { name: 'T2', bus0: 'A', bus1: 'N' }] }))
    expect(f.map(x => [x.kind, x.component.name])).toEqual([['transformerSpan', 'T1']])
    expect(f[0].message).toMatch(/over 500 m/)
  })
  it('skips a transformer with an unplaced bus (that is the unplaced row\'s job)', () => {
    const f = topologyCheck(base({ buses: [A, { name: 'U', x: 0, y: 0 }], transformers: [{ name: 'T', bus0: 'A', bus1: 'U' }] }))
    expect(f.map(x => x.kind)).toEqual(['unplaced'])
  })
})

describe('stored length vs geometry', () => {
  const chord = routeLengthKm([[53, 6], [53, 7]])
  it('flags a line whose stored length disagrees with its chord, and one whose route it disagrees with', () => {
    const routed = routeLengthKm([[53, 6], [53.5, 6.5], [53, 7]])
    const f = topologyCheck(base({
      lines: [{ name: 'ok', bus0: 'A', bus1: 'B', length: chord }, { name: 'bad', bus0: 'A', bus1: 'B', length: chord * 2 }],
      links: [{ name: 'K', bus0: 'A', bus1: 'B', length: chord }],   // chord-length, but routed: disagrees
      routes: { 'link:K': [[53.5, 6.5]] },
    }))
    expect(f.map(x => [x.kind, x.component.type, x.component.name])).toEqual([['lengthMismatch', 'Line', 'bad'], ['lengthMismatch', 'Link', 'K']])
    expect(f[0].message).toMatch(/chord on the map/)
    expect(f[1].message).toMatch(/route on the map/)
    expect(routed).toBeGreaterThan(chord)
  })
  it('a link with PyPSA\'s default 0 length is "not measured", not wrong', () => {
    expect(kinds(base({ links: [{ name: 'K', bus0: 'A', bus1: 'B', length: 0 }] }))).toEqual([])
  })
})

describe('a route that leaves its site', () => {
  const site = { id: 's', name: 'Plot', boundary: [[5.99, 52.99], [6.02, 52.99], [6.02, 53.01], [5.99, 53.01]] as [number, number][] }
  const inA = A, inB = { name: 'B2', x: 6.01, y: 53.0 }
  it('flags a routed branch between two buses inside the site whose bend lies outside it', () => {
    const f = topologyCheck(base({
      buses: [inA, inB, B], sites: [site],
      lines: [{ name: 'L', bus0: 'A', bus1: 'B2' }],
      routes: { 'line:L': [[53.05, 6.005]] },   // north of the plot
    }))
    expect(f.map(x => [x.kind, x.severity, x.other])).toEqual([['routeLeavesSite', 'info', 'Plot']])
    expect(f[0].component).toEqual({ type: 'Line', name: 'L' })
  })
  it('a bend inside the site, a chord, or a feeder to a bus outside the site is fine', () => {
    expect(kinds(base({ buses: [inA, inB, B], sites: [site], lines: [{ name: 'L', bus0: 'A', bus1: 'B2' }], routes: { 'line:L': [[53.005, 6.005]] } }))).toEqual([])
    expect(kinds(base({ buses: [inA, inB, B], sites: [site], lines: [{ name: 'L', bus0: 'A', bus1: 'B2' }] }))).toEqual([])
    expect(kinds(base({ buses: [inA, inB, B], sites: [site], lines: [{ name: 'F', bus0: 'A', bus1: 'B', length: routeLengthKm([[53, 6], [53.5, 6.5], [53, 7]]) }], routes: { 'line:F': [[53.5, 6.5]] } }))).toEqual([])
  })
  it('transformers and links are checked too', () => {
    const f = topologyCheck(base({
      buses: [inA, inB], sites: [site],
      transformers: [{ name: 'T', bus0: 'A', bus1: 'B2' }], links: [{ name: 'K', bus0: 'A', bus1: 'B2', length: 0 }],
      routes: { 'tr:T': [[53.05, 6.005]], 'link:K': [[53.05, 6.005]] },
    }))
    // (T's buses are also ~660 m apart, so the span rule fires for it as well.)
    expect(f.filter(x => x.kind === 'routeLeavesSite').map(x => x.component)).toEqual([{ type: 'Link', name: 'K' }, { type: 'Transformer', name: 'T' }])
    expect(f.filter(x => x.kind === 'transformerSpan').map(x => x.component.name)).toEqual(['T'])
  })
})
