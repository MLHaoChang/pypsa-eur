import { describe, expect, it } from 'vitest'
import { retiredSize } from './retired'

// Template-e2e 14: the IPP's wind farm (60 MW) and PV plant read "retired" (GK B2 (3) d).
describe('retiredSize', () => {
  it('retires nothing before a solve: the unsolved p_nom_opt of 0 is no retirement', () => {
    expect(retiredSize(false, 60, 0, 60)).toBeNull()
  })
  it('never retires an asset pinned at its size', () => {
    expect(retiredSize(true, 60, 0, 60)).toBeNull()
    expect(retiredSize(true, 60, 59, 60)).toBeNull()
  })
  it('retires what a solve took off an asset free to shrink (p_nom_min = 0)', () => {
    expect(retiredSize(true, 60, 20, 0)).toBe(40)
    expect(retiredSize(true, 60, 20, null)).toBe(40)
  })
  it('retires nothing on an unsolved network even where p_nom_min = 0', () => {
    expect(retiredSize(false, 60, 0, 0)).toBeNull()
  })
  it('reads a kept size, a grown one and a missing result as no retirement', () => {
    expect(retiredSize(true, 60, 60, 0)).toBeNull()
    expect(retiredSize(true, 60, 80, 0)).toBeNull()
    expect(retiredSize(true, 60, null, 0)).toBeNull()
    expect(retiredSize(true, 60, Number.NaN, 0)).toBeNull()
  })
})
