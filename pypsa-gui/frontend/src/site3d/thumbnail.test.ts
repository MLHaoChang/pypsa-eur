// Visual-layers plan 3 S5: the site thumbnail's pure decisions.
import { describe, it, expect } from 'vitest'
import { captureDue, thumbSize, thumbnailUrl, THUMB_MAX_PX } from './thumbnail'

describe('thumbSize', () => {
  it('scales the long side down to the cap, keeps the aspect, never scales up', () => {
    expect(thumbSize(2400, 1600)).toEqual({ w: 1200, h: 800 })
    expect(thumbSize(800, 2400)).toEqual({ w: 400, h: 1200 })
    expect(thumbSize(600, 400)).toEqual({ w: 600, h: 400 })
    expect(thumbSize(3000, 10, 100)).toEqual({ w: 100, h: 1 })
  })
  it('an empty canvas is an empty size', () => {
    expect(thumbSize(0, 500)).toEqual({ w: 0, h: 0 })
    expect(thumbSize(NaN, 500)).toEqual({ w: 0, h: 0 })
  })
  it('the cap is a size a report figure can use', () => {
    expect(THUMB_MAX_PX).toBeGreaterThanOrEqual(1000)
  })
})

describe('captureDue', () => {
  it('once per fresh solve: due when the solve is fresh and not yet captured, never twice, never unsolved', () => {
    expect(captureDue(true, 1700, 0)).toBe(true)
    expect(captureDue(true, 1700, 1700)).toBe(false)
    expect(captureDue(true, 1800, 1700)).toBe(true)
    expect(captureDue(false, 1800, 0)).toBe(false)
    expect(captureDue(true, 0, 0)).toBe(false)
  })
})

describe('thumbnailUrl', () => {
  it('encodes the project and site and carries the version', () => {
    expect(thumbnailUrl('my project', 'site_a', 3)).toBe('/api/projects/my%20project/sites/site_a/thumbnail?v=3')
  })
})
