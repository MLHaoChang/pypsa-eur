import { describe, it, expect } from 'vitest'
import {
  RAIL_MIN_W,
  desiredFromDrag,
  railCeiling,
  renderedRailWidth,
} from './railWidth'

// The write path and the render path are tested separately here BECAUSE they
// are separate — the whole design is that the write path cannot see what the
// render path knows. A row that passed a wrapper width to `desiredFromDrag`
// would not compile, which is the point.
//
// The `nextDesiredRailWidth` rows that used to live here (pinned-at-ceiling
// suppression, the guard's `>` boundary, path-independence) are deleted rather
// than left inert: that function is gone, and with it the pinning concept it
// encoded. Their protection is now structural.

describe('railCeiling', () => {
  it.each([
    // wrapW, expected
    [1440, 1080],
    [1200, 840],
    [820, 460],   // 1440 viewport, sidebar 240, dock 380
    [720, 360],   // the hinge: below this the floor wins
    [660, RAIL_MIN_W], // 1280 viewport, sidebar 240, dock 380
    [400, RAIL_MIN_W],
    [0, RAIL_MIN_W],
  ])('wrapW %i → %i', (wrapW, expected) => {
    expect(railCeiling(wrapW)).toBe(expected)
  })
})

describe('renderedRailWidth', () => {
  it('renders the desired width when it fits', () => {
    expect(renderedRailWidth(700, 1440)).toBe(700)
  })

  it('constrains to the ceiling when it does not', () => {
    expect(renderedRailWidth(700, 820)).toBe(460)
  })

  it('constrains a desired width that exceeds the whole wrapper', () => {
    // Reachable now by design: a drag on a constrained rail stores wider than
    // fits. The render absorbing that without complaint is what makes storing
    // it safe.
    expect(renderedRailWidth(5000, 820)).toBe(460)
  })

  it('never goes below the floor', () => {
    expect(renderedRailWidth(700, 660)).toBe(RAIL_MIN_W)
    expect(renderedRailWidth(100, 1440)).toBe(RAIL_MIN_W)
  })
})

describe('desiredFromDrag', () => {
  // Signature is (storedAtStart, startW, delta). No wrapper width, no ceiling.

  describe('on an UNCONSTRAINED rail (startW === storedAtStart)', () => {
    it('records exactly where the user dragged to', () => {
      expect(desiredFromDrag(700, 700, +200)).toBe(900)
      expect(desiredFromDrag(700, 700, -200)).toBe(500)
      expect(desiredFromDrag(700, 700, 0)).toBe(700)
    })

    it('floors at RAIL_MIN_W', () => {
      expect(desiredFromDrag(700, 700, -600)).toBe(RAIL_MIN_W)
    })

    it('records widths larger than any plausible wrapper, unchanged', () => {
      // The intended consequence: asking for wider than fits is stored
      // faithfully, and the render shows what fits.
      expect(desiredFromDrag(700, 700, +2300)).toBe(3000)
    })
  })

  describe('on a CONSTRAINED rail (startW < storedAtStart)', () => {
    // wrapper 820 → ceiling 460, stored 700, so startW = 460.
    const stored = 700
    const startW = 460

    it('never records less than the user already had when asked to WIDEN', () => {
      // The defect this branch exists for. `startW + delta` is 461 here, and
      // recording it would drop a 700px preference to 461 — 239px lost, in
      // the opposite direction from the gesture, with zero visible change
      // because the rail renders 460 either way.
      expect(desiredFromDrag(stored, startW, +1)).toBe(700)
      expect(desiredFromDrag(stored, startW, +2)).toBe(700)
      expect(desiredFromDrag(stored, startW, +50)).toBe(700)
      expect(desiredFromDrag(stored, startW, +200)).toBe(700)
    })

    it('is worst at the smallest gestures, which is why the boundary matters', () => {
      // Without the guard the recorded value would be startW + delta, so the
      // loss is `stored - (startW + delta)`: 239 at 1px, 0 only once the drag
      // exceeds the constraint gap (240 here). Trackpad click-drags routinely
      // carry 1-3px.
      for (const delta of [1, 2, 3]) {
        expect(desiredFromDrag(stored, startW, delta)).toBe(stored)
      }
    })

    it('treats a return to the origin as asking for no change', () => {
      // `>= 0`, not `> 0`. A drag out and back asks for nothing, and must not
      // become a write that drops to the constrained width.
      expect(desiredFromDrag(stored, startW, 0)).toBe(700)
    })

    it('grows past the stored width once the gesture asks for more than it', () => {
      expect(desiredFromDrag(stored, startW, +500)).toBe(960)
      expect(desiredFromDrag(stored, startW, +240)).toBe(700)
      expect(desiredFromDrag(stored, startW, +241)).toBe(701)
    })

    it('records a SHRINK exactly as asked', () => {
      // Nothing to protect: the user asked for narrower, it is visible on
      // screen, and the guard must not swallow it.
      expect(desiredFromDrag(stored, startW, -100)).toBe(RAIL_MIN_W)
      expect(desiredFromDrag(stored, startW, -1)).toBe(459)
    })
  })
})
