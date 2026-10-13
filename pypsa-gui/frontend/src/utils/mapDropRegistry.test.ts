// The map drop slot (plan M4). A stale canvas unmount must not clear a
// newer mount, or a palette drop lands on no map (or on the one that just
// went away). The site canvas's twin is covered in site3d/dropRegistry.test.ts.
import { beforeEach, describe, expect, it } from 'vitest'
import {
  mapDropTarget,
  registerMapDropTarget,
  unregisterMapDropTarget,
  type MapDropTarget,
} from './mapDropRegistry'

const target = (lat: number): MapDropTarget => ({
  screenToLatLng: () => ({ lat, lng: 0 }),
})

describe('map drop registry', () => {
  beforeEach(() => {
    const cur = mapDropTarget()
    if (cur) unregisterMapDropTarget(cur)
  })

  it('is empty until a canvas registers, and empty again after it unregisters', () => {
    expect(mapDropTarget()).toBeNull()
    const a = target(1)
    registerMapDropTarget(a)
    expect(mapDropTarget()).toBe(a)
    unregisterMapDropTarget(a)
    expect(mapDropTarget()).toBeNull()
  })

  it('a stale unmount does not clear a newer mount', () => {
    const a = target(1)
    const b = target(2)
    registerMapDropTarget(a)
    registerMapDropTarget(b)
    unregisterMapDropTarget(a)
    expect(mapDropTarget()).toBe(b)
    expect(mapDropTarget()?.screenToLatLng(0, 0)).toEqual({ lat: 2, lng: 0 })
    unregisterMapDropTarget(b)
    expect(mapDropTarget()).toBeNull()
  })

  it('unregistering a target that was never current leaves the slot alone', () => {
    const a = target(1)
    const stranger = target(9)
    registerMapDropTarget(a)
    unregisterMapDropTarget(stranger)
    expect(mapDropTarget()).toBe(a)
    unregisterMapDropTarget(a)
  })
})
