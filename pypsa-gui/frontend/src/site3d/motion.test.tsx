// Phase 2 plan Task 6.1 / spec E11: values ease towards a snapshot's target
// over ~300 ms; reduced motion applies targets at once and holds rotors still.
import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { easeTowards, motionFor, useReducedMotion, EASE_TAU_MS, rotorStep, flyStart, flyPose, flyDuration, easeOutCubic, FLY_IN_MS } from './motion'

describe('fly-in (plan 3 S4)', () => {
  const to = { position: [100, 300, 500] as [number, number, number], target: [100, 0, 100] as [number, number, number] }
  it('starts straight above the target, higher than the fitted distance, looking at the same target', () => {
    const from = flyStart(to)
    expect(from.target).toEqual(to.target)
    expect(from.position[0]).toBe(100)
    const dist = Math.hypot(0, 300, 400)
    expect(from.position[1]).toBeCloseTo(dist * 1.6)
    expect(Math.abs(from.position[2] - 100)).toBeLessThan(dist)   // a nudge, not an offset
  })
  it('eases out: past halfway in time it is well past halfway in space, never overshoots, lands on the target', () => {
    const from = flyStart(to)
    const half = flyPose(from, to, FLY_IN_MS / 2)
    const share = (half.pose.position[1] - from.position[1]) / (to.position[1] - from.position[1])
    expect(share).toBeGreaterThan(0.8)
    expect(half.done).toBe(false)
    const end = flyPose(from, to, FLY_IN_MS)
    expect(end.pose).toEqual(to)
    expect(end.done).toBe(true)
    expect(flyPose(from, to, FLY_IN_MS * 3).pose).toEqual(to)
    expect(flyPose(from, to, 0).pose).toEqual(from)
  })
  it('a zero duration is a jump cut; reduced motion asks for one', () => {
    const from = flyStart(to)
    expect(flyPose(from, to, 0, 0)).toEqual({ pose: to, done: true })
    expect(flyDuration(true)).toBe(0)
    expect(flyDuration(false)).toBe(FLY_IN_MS)
  })
  it('easeOutCubic is monotonic on [0, 1] and clamps outside it', () => {
    let prev = 0
    for (let u = 0; u <= 1; u += 0.05) { const v = easeOutCubic(u); expect(v).toBeGreaterThanOrEqual(prev); prev = v }
    expect(easeOutCubic(-1)).toBe(0)
    expect(easeOutCubic(2)).toBe(1)
  })
})

describe('easeTowards', () => {
  it('converges on the target and never overshoots, from either side', () => {
    let v = 0
    for (let i = 0; i < 60; i++) {
      const nv = easeTowards(v, 1, 16, EASE_TAU_MS)
      expect(nv).toBeGreaterThanOrEqual(v); expect(nv).toBeLessThanOrEqual(1)
      v = nv
    }
    expect(v).toBeGreaterThan(0.95)                        // ~1 s at 60 fps: settled
    let w = 1
    for (let i = 0; i < 60; i++) { const nw = easeTowards(w, 0, 16, EASE_TAU_MS); expect(nw).toBeLessThanOrEqual(w); expect(nw).toBeGreaterThanOrEqual(0); w = nw }
  })
  it('a huge frame gap lands on the target; zero dt leaves the value; tau 0 is instant', () => {
    expect(easeTowards(0, 5, 1e6, EASE_TAU_MS)).toBe(5)
    expect(easeTowards(2, 5, 0, EASE_TAU_MS)).toBe(2)
    expect(easeTowards(2, 5, 16, 0)).toBe(5)
  })
  it('snaps when within a hair of the target (no endless tiny updates)', () => {
    expect(easeTowards(0.99999, 1, 16, EASE_TAU_MS)).toBe(1)
  })
})

describe('motionFor', () => {
  it('reduced: no easing and no spin; otherwise the easing constant and spin on', () => {
    expect(motionFor(true)).toEqual({ tauMs: 0, spin: false })
    expect(motionFor(false)).toEqual({ tauMs: EASE_TAU_MS, spin: true })
  })
  it('rotorStep turns rpm into radians per frame, and nothing when spin is off', () => {
    expect(rotorStep(60, 1000, true)).toBeCloseTo(2 * Math.PI)    // 60 rpm = 1 turn/s
    expect(rotorStep(60, 1000, false)).toBe(0)
    expect(rotorStep(0, 1000, true)).toBe(0)
  })
})

describe('useReducedMotion', () => {
  const orig = window.matchMedia
  afterEach(() => { window.matchMedia = orig })
  it('reads prefers-reduced-motion and follows its change events', () => {
    let listener: ((e: MediaQueryListEvent) => void) | null = null
    window.matchMedia = vi.fn((q: string) => ({
      matches: q.includes('reduce'), media: q,
      addEventListener: (_: string, l: (e: MediaQueryListEvent) => void) => { listener = l },
      removeEventListener: vi.fn(),
    })) as never
    const { result } = renderHook(() => useReducedMotion())
    expect(result.current).toBe(true)
    act(() => { listener!({ matches: false } as MediaQueryListEvent) })
    expect(result.current).toBe(false)
  })
  it('without matchMedia (old jsdom, SSR) it is false', () => {
    window.matchMedia = undefined as never
    const { result } = renderHook(() => useReducedMotion())
    expect(result.current).toBe(false)
  })
})
