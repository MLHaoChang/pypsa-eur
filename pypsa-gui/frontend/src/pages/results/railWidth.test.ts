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
  it('records exactly where the user dragged to', () => {
    expect(desiredFromDrag(700)).toBe(700)
    expect(desiredFromDrag(1234)).toBe(1234)
  })

  it('floors at RAIL_MIN_W', () => {
    expect(desiredFromDrag(100)).toBe(RAIL_MIN_W)
    expect(desiredFromDrag(-50)).toBe(RAIL_MIN_W)
  })

  it('records widths larger than any plausible wrapper, unchanged', () => {
    // The intended consequence. Dragging left on a constrained rail asks for
    // wider than currently fits; storing it faithfully is what lets the user
    // get that width back when the dock closes. There is no ceiling to clamp
    // against here — that is the entire fix.
    expect(desiredFromDrag(3000)).toBe(3000)
  })

  it('depends on nothing but the released position', () => {
    // Structural, not behavioural: the function takes one argument, so no
    // stale wrapper, no live store read, and no gesture history can reach it.
    // Every previous defect in this logic entered through one of those.
    expect(desiredFromDrag.length).toBe(1)
  })
})
