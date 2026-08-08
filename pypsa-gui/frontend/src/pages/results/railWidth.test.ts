import { describe, it, expect } from 'vitest'
import {
  RAIL_MIN_W,
  nextDesiredRailWidth,
  railCeiling,
  renderedRailWidth,
} from './railWidth'

// The table test the extraction exists to make possible. Every one of these
// rows was previously reachable only by simulating a DOM drag.

describe('railCeiling', () => {
  it.each([
    // wrapW, expected
    [1440, 1080],
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

  it('never goes below the floor', () => {
    expect(renderedRailWidth(700, 660)).toBe(RAIL_MIN_W)
    expect(renderedRailWidth(100, 1440)).toBe(RAIL_MIN_W)
  })
})

describe('nextDesiredRailWidth', () => {
  // ── Records normally ──────────────────────────────────────────────────
  it('records a release that lands short of the ceiling', () => {
    expect(nextDesiredRailWidth(700, 1440, 500)).toBe(500)
  })

  it('records a deliberate shrink', () => {
    expect(nextDesiredRailWidth(700, 1440, 400)).toBe(400)
  })

  it('clamps a release below the floor up to the floor, and records it', () => {
    expect(nextDesiredRailWidth(700, 1440, 100)).toBe(RAIL_MIN_W)
  })

  it('records growing INTO the ceiling from a smaller stored width', () => {
    // 400 stored, ceiling 460, released past it. The user is asking for the
    // widest rail that fits — a real choice, not the constraint deciding.
    expect(nextDesiredRailWidth(400, 820, 900)).toBe(460)
  })

  it('records landing exactly on the ceiling from below', () => {
    expect(nextDesiredRailWidth(400, 820, 460)).toBe(460)
  })

  // ── Records nothing ───────────────────────────────────────────────────
  it('records nothing when pinned at the ceiling with a larger width stored', () => {
    // The reported case (a): desired 700, wrapper 820, ceiling 460. Any
    // release at or beyond the ceiling is the constraint, not a choice.
    expect(nextDesiredRailWidth(700, 820, 900)).toBeNull()
    expect(nextDesiredRailWidth(700, 820, 460)).toBeNull()
  })

  it('records nothing on a wrapper too narrow to honour any drag', () => {
    // The reported case (b): wrapper 660, so ceiling === floor === 360 and
    // EVERY release resolves to it. Nothing here is a choice.
    expect(nextDesiredRailWidth(700, 660, 900)).toBeNull()
    expect(nextDesiredRailWidth(700, 660, 500)).toBeNull()
    expect(nextDesiredRailWidth(700, 660, 100)).toBeNull()
  })

  // ── The gesture-level property ────────────────────────────────────────
  // These are the same function call the ratchet used to defeat by feeding
  // the guard its own intermediate output. Held per gesture, the answer does
  // not depend on how the pointer travelled to get there.
  it('is unaffected by where the pointer went mid-gesture', () => {
    // Release pinned. Whether the user drifted 1px sub-ceiling on the way is
    // not represented here at all — which is the point of deciding once.
    const desiredAtStart = 700
    expect(nextDesiredRailWidth(desiredAtStart, 820, 660)).toBeNull()
    expect(nextDesiredRailWidth(desiredAtStart, 820, 459)).toBe(459)
    expect(nextDesiredRailWidth(desiredAtStart, 820, 660)).toBeNull()
  })

  it('does not fire the guard when stored equals the ceiling', () => {
    // `>` not `>=`: with nothing larger stored there is no preference to
    // protect, and skipping the write would make the rail unresizable.
    expect(nextDesiredRailWidth(460, 820, 900)).toBe(460)
  })
})
