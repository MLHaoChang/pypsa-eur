// Phase 2 plan Task 2.2 (and WP5 later): how an object glows. One place
// decides it, with a fixed precedence: selected > outside the boundary >
// hovered > outside the boundary > a result.
import { describe, it, expect } from 'vitest'
import { emissiveFor, OUTSIDE_COLOR, HOVER_COLOR } from './resultStyle'

describe('emissiveFor', () => {
  it('nothing on: no glow', () => {
    expect(emissiveFor({ selected: false, hovered: false, outside: false }, '#7c3aed').intensity).toBe(0)
  })
  it('selected glows in the object\'s colour, over everything else', () => {
    expect(emissiveFor({ selected: true, hovered: true, outside: true }, '#7c3aed', 0.9)).toEqual({ color: '#7c3aed', intensity: 0.6 })
  })
  it('hovered glows warm, over the outside warning and a result (hover is transient; the label says "outside")', () => {
    expect(emissiveFor({ selected: false, hovered: true, outside: true }, '#7c3aed', 0.9)).toEqual({ color: HOVER_COLOR, intensity: 0.25 })
  })
  it('outside the boundary glows red, over a result', () => {
    expect(emissiveFor({ selected: false, hovered: false, outside: true }, '#7c3aed', 0.9)).toEqual({ color: OUTSIDE_COLOR, intensity: 0.45 })
  })
  it('a result glows in the object\'s colour, scaled and clamped', () => {
    expect(emissiveFor({ selected: false, hovered: false, outside: false }, '#16a34a', 0.5)).toEqual({ color: '#16a34a', intensity: 0.25 })
    expect(emissiveFor({ selected: false, hovered: false, outside: false }, '#16a34a', 7).intensity).toBe(0.5)
    expect(emissiveFor({ selected: false, hovered: false, outside: false }, '#16a34a', -1).intensity).toBe(0)
  })
})
