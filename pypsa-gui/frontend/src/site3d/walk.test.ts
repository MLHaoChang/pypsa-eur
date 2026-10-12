// Visual-layers plan 3 S5: the walk mode's pure step.
import { describe, it, expect } from 'vitest'
import { walkDelta, WALK_KEYS, WALK_SPEED_MPS, EYE_HEIGHT_M, type WalkKey } from './walk'

const held = (...k: WalkKey[]) => new Set(k)
const north = { x: 0, y: 1 }

describe('walkDelta', () => {
  it('forward walks along the view direction at walking pace; back the other way', () => {
    expect(walkDelta(held('forward'), north, 1)).toEqual({ x: 0, y: WALK_SPEED_MPS })
    const b = walkDelta(held('back'), north, 0.5)
    expect(b.x).toBeCloseTo(0); expect(b.y).toBeCloseTo(-WALK_SPEED_MPS / 2)
  })
  it('right is clockwise of forward (facing north, right is east); the view length does not matter', () => {
    const r = walkDelta(held('right'), { x: 0, y: 7 }, 1)
    expect(r.x).toBeCloseTo(WALK_SPEED_MPS); expect(r.y).toBeCloseTo(0)
    const l = walkDelta(held('left'), { x: 1, y: 0 }, 1)   // facing east, left is north
    expect(l.x).toBeCloseTo(0); expect(l.y).toBeCloseTo(WALK_SPEED_MPS)
  })
  it('a diagonal is no faster than a straight line, opposite keys cancel, nothing held stands still', () => {
    const d = walkDelta(held('forward', 'right'), north, 1)
    expect(Math.hypot(d.x, d.y)).toBeCloseTo(WALK_SPEED_MPS)
    expect(walkDelta(held('forward', 'back'), north, 1)).toEqual({ x: 0, y: 0 })
    expect(walkDelta(held(), north, 1)).toEqual({ x: 0, y: 0 })
  })
  it('looking straight down (no ground direction) or a zero frame moves nothing', () => {
    expect(walkDelta(held('forward'), { x: 0, y: 0 }, 1)).toEqual({ x: 0, y: 0 })
    expect(walkDelta(held('forward'), north, 0)).toEqual({ x: 0, y: 0 })
  })
  it('WASD and the arrows are the keys; the eye is 1.7 m up', () => {
    expect(WALK_KEYS.KeyW).toBe('forward'); expect(WALK_KEYS.ArrowDown).toBe('back')
    expect(WALK_KEYS.KeyA).toBe('left'); expect(WALK_KEYS.ArrowRight).toBe('right')
    expect(EYE_HEIGHT_M).toBe(1.7)
  })
})
