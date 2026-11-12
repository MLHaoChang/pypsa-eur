import { describe, expect, it } from 'vitest'
import { PNG_MAX_PIXELS, PNG_MAX_SIDE, pngSize } from './chartPng'

describe('pngSize', () => {
  it('draws at 2x when it fits', () => {
    expect(pngSize(600, 300)).toEqual({ w: 1200, h: 600 })
  })
  it('stays inside the side and pixel caps', () => {
    for (const [w, h] of [[20000, 100], [6000, 6000], [9000, 9000]]) {
      const s = pngSize(w, h)
      expect(s.w).toBeLessThanOrEqual(PNG_MAX_SIDE)
      expect(s.h).toBeLessThanOrEqual(PNG_MAX_SIDE)
      expect(s.w * s.h).toBeLessThanOrEqual(PNG_MAX_PIXELS)
    }
  })
})
