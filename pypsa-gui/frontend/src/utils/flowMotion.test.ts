// Plan 1 A3: the speed bucket is a pure function of |p| / rating, five
// buckets, clamped, and the CSS it drives is one block.
import { describe, it, expect } from 'vitest'
import {
  FLOW_BUCKET_PERIOD_S, FLOW_CYCLE_PX, FLOW_SPEED_BUCKETS, flowAnimationCss, flowClass, flowCycleValue, flowDirection,
  flowShare, flowSpeedBucket, flowWidth,
} from './flowMotion'

describe('flowSpeedBucket', () => {
  it('is idle at zero flow and at a non-number', () => {
    expect(flowSpeedBucket(0, 100)).toBe(0)
    expect(flowSpeedBucket(1e-4, 100)).toBe(0)
    expect(flowSpeedBucket(NaN, 100)).toBe(0)
  })

  it('quantises the share of rating in fifths, clamped to five', () => {
    expect([10, 20, 21, 40, 41, 60, 61, 80, 81, 100, 170].map(p => flowSpeedBucket(p, 100))).toEqual([1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 5])
    expect(FLOW_SPEED_BUCKETS).toBe(5)
  })

  it('direction does not change the speed', () => {
    expect(flowSpeedBucket(-45, 100)).toBe(flowSpeedBucket(45, 100))
  })

  it('a flow without a rating moves at the slowest speed', () => {
    expect(flowSpeedBucket(12, null)).toBe(1)
    expect(flowSpeedBucket(12, 0)).toBe(1)
    expect(flowSpeedBucket(12, undefined)).toBe(1)
  })

  it('the share the width reads is the same clamped |p| / rating the 3D rotor turns by', () => {
    expect(flowShare(50, 100)).toBe(0.5)
    expect(flowShare(-50, 100)).toBe(0.5)
    expect(flowShare(170, 100)).toBe(1)
    expect(flowShare(5, 0)).toBe(0)
    expect(flowWidth(0)).toBe(1.5)
    expect(flowWidth(1)).toBe(7.5)
    expect(flowWidth(2)).toBe(7.5)
  })
})

describe('what an edge carries', () => {
  it('a class per bucket and nothing when idle', () => {
    expect(flowClass(0)).toBe('')
    expect(flowClass(3)).toBe('canvas-flow flow-b3')
  })

  it('the cycle variable moves the dash along the path for a forward flow and back for a reverse one', () => {
    expect(flowDirection(12)).toBe(1)
    expect(flowDirection(-12)).toBe(-1)
    expect(flowCycleValue(1)).toBe(`-${FLOW_CYCLE_PX}px`)
    expect(flowCycleValue(-1)).toBe(`${FLOW_CYCLE_PX}px`)
  })

  it('one style block: one keyframe, a duration class per bucket, faster with the bucket, and the reduced-motion stop', () => {
    const css = flowAnimationCss()
    expect(css.match(/@keyframes/g)).toHaveLength(1)
    for (let b = 1; b <= 5; b++) expect(css).toContain(`.flow-b${b} { animation-duration: ${FLOW_BUCKET_PERIOD_S[b]}s; }`)
    for (let b = 1; b < 5; b++) expect(FLOW_BUCKET_PERIOD_S[b]).toBeGreaterThan(FLOW_BUCKET_PERIOD_S[b + 1])
    expect(css).toContain('@media (prefers-reduced-motion: reduce) { .canvas-flow { animation: none; } }')
  })
})
