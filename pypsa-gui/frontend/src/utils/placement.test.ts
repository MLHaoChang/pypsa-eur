// The click-to-place queue is the one part of the unplaced-buses-map change
// that was deliberately redesigned away from the original plan (a numeric
// skip-index), and it shipped with zero automated coverage. These functions
// are pure and import nothing, so there is no excuse for that — see
// docs/superpowers/specs/2026-07-30-unplaced-buses-map-design.md.
import { describe, expect, it } from 'vitest'
import { canSkip, nextBusToPlace } from './placement'

describe('nextBusToPlace', () => {
  it('returns undefined when there is nothing left to place', () => {
    expect(nextBusToPlace([], new Set())).toBeUndefined()
  })

  it('walks the queue in order as buses are placed one at a time', () => {
    // Simulates "place x3": after each placement the placed bus drops out of
    // `unplaced` (the buses query refetches), and the picker advances to the
    // next name without ever repeating or skipping one.
    let unplaced = ['B1', 'B2', 'B3']
    const skipped = new Set<string>()

    expect(nextBusToPlace(unplaced, skipped)).toBe('B1')
    unplaced = unplaced.filter(n => n !== 'B1')

    expect(nextBusToPlace(unplaced, skipped)).toBe('B2')
    unplaced = unplaced.filter(n => n !== 'B2')

    expect(nextBusToPlace(unplaced, skipped)).toBe('B3')
    unplaced = unplaced.filter(n => n !== 'B3')

    // Queue terminates: nothing left, nothing to return.
    expect(nextBusToPlace(unplaced, skipped)).toBeUndefined()
  })

  it('resurfaces a skipped bus once every other bus has been placed', () => {
    // Skip B1, place B2, place B3 — B1 was never placed, so it must be the
    // only name left and the picker must return to it rather than reporting
    // "done" or losing it.
    let unplaced = ['B1', 'B2', 'B3']
    const skipped = new Set(['B1'])

    expect(nextBusToPlace(unplaced, skipped)).toBe('B2')
    unplaced = unplaced.filter(n => n !== 'B2')

    expect(nextBusToPlace(unplaced, skipped)).toBe('B3')
    unplaced = unplaced.filter(n => n !== 'B3')

    // Only the skipped bus remains — it resurfaces via the `unplaced[0]`
    // fallback rather than the picker reporting nothing left.
    expect(unplaced).toEqual(['B1'])
    expect(nextBusToPlace(unplaced, skipped)).toBe('B1')
  })

  it('skipping every remaining bus falls back to the head of the queue — no bus is stranded', () => {
    const unplaced = ['B1', 'B2', 'B3']
    const skipped = new Set(['B1', 'B2', 'B3'])

    // `.find` finds nothing (every name is skipped), so the `?? unplaced[0]`
    // fallback fires. The picker never returns undefined while buses remain.
    expect(nextBusToPlace(unplaced, skipped)).toBe('B1')
  })
})

describe('canSkip', () => {
  it('is true while at least one remaining bus is not yet skipped', () => {
    expect(canSkip(['B1', 'B2', 'B3'], new Set())).toBe(true)
    expect(canSkip(['B1', 'B2', 'B3'], new Set(['B1']))).toBe(true)
    expect(canSkip(['B1', 'B2', 'B3'], new Set(['B1', 'B2']))).toBe(true)
  })

  it('is false once every remaining bus has been skipped', () => {
    expect(canSkip(['B1', 'B2', 'B3'], new Set(['B1', 'B2', 'B3']))).toBe(false)
    expect(canSkip(['B1'], new Set(['B1']))).toBe(false)
  })

  it('is false for an empty queue — nothing left to skip', () => {
    expect(canSkip([], new Set())).toBe(false)
  })
})
