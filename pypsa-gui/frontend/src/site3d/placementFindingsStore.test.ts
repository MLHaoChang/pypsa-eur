import { beforeEach, describe, expect, it } from 'vitest'
import { componentOfKey, usePlacementFindings } from './placementFindingsStore'

beforeEach(() => usePlacementFindings.getState().clear())

describe('placementFindingsStore', () => {
  it('starts empty, publishes a site\'s findings, clears', () => {
    expect(usePlacementFindings.getState()).toMatchObject({ site: null, findings: [] })
    const f = [{ key: 'Generator:GEN', kind: 'keepOut' as const, severity: 'warn' as const, message: 'GEN is 10 m from HALL; it should keep 30 m away', other: 'Load:HALL' }]
    usePlacementFindings.getState().publish({ id: 'a', name: 'Campus' }, f)
    expect(usePlacementFindings.getState().site).toEqual({ id: 'a', name: 'Campus' })
    expect(usePlacementFindings.getState().findings).toBe(f)
    usePlacementFindings.getState().clear()
    expect(usePlacementFindings.getState().site).toBeNull()
    expect(usePlacementFindings.getState().findings).toEqual([])
  })
  it('componentOfKey splits at the first colon only (a name may contain one)', () => {
    expect(componentOfKey('Transformer:TR 110/33:a')).toEqual({ type: 'Transformer', name: 'TR 110/33:a' })
    expect(componentOfKey('Bus:MV')).toEqual({ type: 'Bus', name: 'MV' })
  })
})
