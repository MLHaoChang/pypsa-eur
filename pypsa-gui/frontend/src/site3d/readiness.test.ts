// Visual-layers plan 3 S4: the readiness checklist the empty state renders.
import { describe, it, expect } from 'vitest'
import { connectedBranches, siteReadiness } from './readiness'

const B = (name: string, placed = true) => ({ name, x: placed ? 6.8 : null, y: placed ? 53.4 : null })

describe('connectedBranches', () => {
  it('counts a branch only when both ends name a bus of the model and differ', () => {
    const buses = [B('A'), B('B')]
    expect(connectedBranches({ buses, branches: [{ bus0: 'A', bus1: 'B' }] })).toBe(1)
    expect(connectedBranches({ buses, branches: [{ bus0: 'A', bus1: 'A' }] })).toBe(0)
    expect(connectedBranches({ buses, branches: [{ bus0: 'A', bus1: 'gone' }] })).toBe(0)
    expect(connectedBranches({ buses, branches: [] })).toBe(0)
  })
})

describe('siteReadiness', () => {
  it('an empty model: nothing met, every fix points at placing a bus or connecting', () => {
    const r = siteReadiness({ buses: [], branches: [], sites: [] })
    expect(r.ready).toBe(false)
    expect(r.lines.map(l => [l.id, l.met])).toEqual([['coordinates', false], ['branch', false], ['boundary', false]])
    expect(r.lines[0].text).toBe('No bus in the model yet')
    expect(r.lines[0].fix).toBe('placeBus')
    expect(r.lines[1].fix).toBe('connect')
    expect(r.lines[2].fix).toBe('placeBus')
    expect(r.placedBuses).toEqual([])
  })

  it('buses without coordinates: the coordinates and boundary lines are unmet and say how many', () => {
    const r = siteReadiness({ buses: [B('A', false), B('B', false)], branches: [{ bus0: 'A', bus1: 'B' }], sites: [] })
    expect(r.ready).toBe(false)
    expect(r.lines[0]).toMatchObject({ met: false, text: 'None of the 2 buses has coordinates' })
    expect(r.lines[1]).toMatchObject({ met: true, text: '1 branch connects two buses' })
    expect(r.lines[2]).toMatchObject({ met: false, fix: 'placeBus' })
  })

  it('one placed bus and one branch: ready; the boundary line offers Generate around the first placed bus', () => {
    const r = siteReadiness({ buses: [B('A', false), B('B'), B('C')], branches: [{ bus0: 'B', bus1: 'C' }, { bus0: 'A', bus1: 'B' }], sites: [] })
    expect(r.ready).toBe(true)
    expect(r.lines[0].text).toBe('2 of 3 buses have coordinates')
    expect(r.lines[1].text).toBe('2 branches connect two buses')
    expect(r.lines[2]).toMatchObject({ met: true, fix: 'generate', fixLabel: 'Generate 3D site' })
    expect(r.lines[2].text).toContain('around B')
    expect(r.placedBuses).toEqual(['B', 'C'])
  })

  it('a placed bus but no branch is not ready: the connect line is the one to fix', () => {
    const r = siteReadiness({ buses: [B('A')], branches: [], sites: [] })
    expect(r.ready).toBe(false)
    expect(r.lines.filter(l => !l.met).map(l => l.id)).toEqual(['branch'])
  })

  it('an existing site names itself on the boundary line', () => {
    const r = siteReadiness({ buses: [B('A'), B('B')], branches: [{ bus0: 'A', bus1: 'B' }], sites: [{ name: 'Campus' }] })
    expect(r.lines[2].text).toBe('Site boundary: Campus')
    expect(r.ready).toBe(true)
  })
})
