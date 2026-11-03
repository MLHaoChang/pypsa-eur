import { describe, expect, it } from 'vitest'
import type { PlaceEntry } from '../api/siteScene'
import { clickPicks, detachRefusalCopy, detachTitle, groupCentre, moveGroup, undoTitle, UNDO_GONE } from './groupEdit'

const P: Record<string, PlaceEntry> = {
  'StorageUnit:bess': { x: 0, y: 0, heading: 0, on: 'ground' },
  'Generator:gas': { x: 10, y: 0, heading: 90, on: 'ground' },
  'Generator:solar': { x: 5, y: 5, heading: 0, on: 'roof:hall' },
}

describe('moveGroup', () => {
  it('moves every picked item by the same east/north offset', () => {
    const out = moveGroup(P, ['StorageUnit:bess', 'Generator:gas'], { east: 3, north: -2, rotate: 0 })
    expect(out).toEqual({
      'StorageUnit:bess': { x: 3, y: -2, heading: 0, on: 'ground' },
      'Generator:gas': { x: 13, y: -2, heading: 90, on: 'ground' },
    })
  })

  it('rotates clockwise about the centre and turns the headings with it', () => {
    // centre (5, 0); 90° clockwise: the west item goes north, the east one south
    const out = moveGroup(P, ['StorageUnit:bess', 'Generator:gas'], { east: 0, north: 0, rotate: 90 })
    expect(out['StorageUnit:bess']).toEqual({ x: 5, y: 5, heading: 90, on: 'ground' })
    expect(out['Generator:gas']).toEqual({ x: 5, y: -5, heading: 180, on: 'ground' })
  })

  it('keeps each item on its roof and wraps the heading into [0, 360)', () => {
    const out = moveGroup(P, ['Generator:solar'], { east: 0, north: 0, rotate: -90 })
    expect(out['Generator:solar']).toEqual({ x: 5, y: 5, heading: 270, on: 'roof:hall' })
  })

  it('leaves out keys that are not placed', () => {
    expect(Object.keys(moveGroup(P, ['Store:tank', 'Generator:gas'], { east: 1, north: 0, rotate: 0 }))).toEqual(['Generator:gas'])
  })

  it('takes the mean position as the centre', () => {
    expect(groupCentre(Object.values(P))).toEqual([5, 5 / 3])
  })
})

describe('detach words', () => {
  it('names the item in every refusal and never the raw key', () => {
    for (const code of ['not_placed', 'duplicate', 'already_placed', 'unbound_target', 'target_changed', 'invalid_allocation']) {
      const text = detachRefusalCopy(code, 'BESS 1')!
      expect(text.startsWith('BESS 1: ')).toBe(true)
      expect(text).not.toMatch(/StorageUnit:|'/)
    }
    expect(detachRefusalCopy('already_placed', 'BESS 1')).toBe('BESS 1: it was placed again since the detach, so it can’t be restored')
    expect(detachRefusalCopy('detach_not_found', null)).toBe(UNDO_GONE)
    expect(detachRefusalCopy('revision_conflict', 'x')).toBeNull()
  })

  it('titles the preview cards', () => {
    expect(detachTitle(2)).toBe('Detach 2 items from the scene. They stay in the model.')
    expect(detachTitle(1)).toBe('Detach 1 item from the scene. It stays in the model.')
    expect(undoTitle(2)).toBe('Undo detach: restore 2 items')
  })
})

describe('canvas click (SiteCanvas onSelectObject)', () => {
  it('Shift+click on a placed item of a scene-enabled Site picks it', () => {
    expect(clickPicks(true, true, true)).toBe(true)
  })
  it.each([[false, true, true], [true, false, true], [true, true, false]])('shift=%s scene=%s placed=%s selects as before', (s, e, p) => {
    expect(clickPicks(s, e, p)).toBe(false)
  })
})
