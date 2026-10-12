// Phase 2 WP6 gate: the results layer's decisions, pure (WebGL cannot run in
// vitest): when decorations must be rebuilt, the gauge bar, the chevron
// path, and the result glow with its precedence.
import { describe, expect, it } from 'vitest'
import { anchorsSignature, gaugeBar, chevronPath, resultGlow } from './resultsDecor'

describe('anchorsSignature', () => {
  it('changes when a gauge or flow anchor changes, not for rotors or emissive', () => {
    const a = { fill: { pos: [0, 0, 3] as [number, number, number], size: [47.4, 1, 0.4] as [number, number, number] } }
    expect(anchorsSignature(a)).toBe(anchorsSignature({ ...a, emissive: true }))
    expect(anchorsSignature(a)).not.toBe(anchorsSignature({ fill: { ...a.fill, size: [17, 1, 0.4] } }))
    expect(anchorsSignature({ flow: { from: [0, -2, 3], to: [0, 2, 3] } })).not.toBe(anchorsSignature({ flow: { from: [0, -3, 3], to: [0, 3, 3] } }))
    expect(anchorsSignature(undefined)).toBe('')
  })
})

describe('gaugeBar', () => {
  it('grows from the west end of the track', () => {
    expect(gaugeBar(0.5, 10)).toEqual({ scaleX: 0.5, x: -2.5 })
    expect(gaugeBar(1, 10)).toEqual({ scaleX: 1, x: 0 })
    const empty = gaugeBar(0, 10)
    expect(empty.scaleX).toBeGreaterThan(0)             // never a zero scale (degenerate matrix)
    expect(empty.x).toBeCloseTo(-5, 2)
    expect(gaugeBar(2, 10)).toEqual({ scaleX: 1, x: 0 })  // clamped
  })
})

describe('chevronPath', () => {
  const from: [number, number, number] = [0, 5, 0], to: [number, number, number] = [0, 5, -10]
  it('dir +1: from → to, evenly spaced by phase, heading along the flow', () => {
    const p = chevronPath(from, to, 1, 0, 4)
    expect(p.points.map(q => q[2] + 0)).toEqual([0, -2.5, -5, -7.5])
    expect(p.heading).toEqual([0, 0, -1])
  })
  it('dir −1 runs back; the phase moves them along and wraps', () => {
    const p = chevronPath(from, to, -1, 0.25, 2)
    expect(p.heading).toEqual([0, 0, 1])
    expect(p.points.map(q => q[2])).toEqual([-7.5, -2.5])
  })
  it('dir 0: none', () => {
    expect(chevronPath(from, to, 0, 0, 4).points).toEqual([])
  })
})

describe('resultGlow', () => {
  it('locked (selected / hovered / outside) → leave the glow to that state', () => {
    expect(resultGlow(true, { emissive: 0.8, label: '' }, 0.8, '#123456')).toBeNull()
  })
  it('a share glows in the object colour, eased; a branch in its band, scaled by loading; nothing → off', () => {
    expect(resultGlow(false, { emissive: 0.8, label: '' }, 0.4, '#123456')).toEqual({ color: '#123456', intensity: 0.2 })
    const b = resultGlow(false, { color: '#16a34a', flow: { dir: 1, pct: 0 }, label: '' }, 0, '#475569')!
    expect(b.color).toBe('#16a34a'); expect(b.intensity).toBeGreaterThan(0); expect(b.intensity).toBeLessThan(0.2)
    expect(resultGlow(false, { color: '#dc2626', flow: { dir: 1, pct: 100 }, label: '' }, 0, '#475569')!.intensity).toBeCloseTo(0.45)
    expect(resultGlow(false, undefined, 0, '#123456')).toEqual({ color: '#000000', intensity: 0 })
  })
})
