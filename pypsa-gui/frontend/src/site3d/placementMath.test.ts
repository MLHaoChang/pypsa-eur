import { describe, it, expect } from 'vitest'
import { Matrix4, Vector3 } from 'three'
import { matrixFor, placementFromMatrix } from './placementMath'

describe('placement ↔ matrix', () => {
  it('round-trips position and heading', () => {
    for (const p of [{ x: 12.5, y: -40, heading: 0 }, { x: -3, y: 7.25, heading: 90 }, { x: 0, y: 0, heading: 237.5 }, { x: 100, y: 100, heading: 359 }]) {
      expect(placementFromMatrix(matrixFor(p))).toEqual(p)
    }
  })

  it('north is −Z in the scene and a 90° heading faces east', () => {
    const m = matrixFor({ x: 0, y: 10, heading: 90 })
    const pos = new Vector3().setFromMatrixPosition(m)
    expect(pos.z).toBeCloseTo(-10)
    // The object's local "north" (−Z) rotated by a clockwise 90° points east (+X).
    const north = new Vector3(0, 0, -1).applyMatrix4(new Matrix4().extractRotation(m))
    expect(north.x).toBeCloseTo(1)
    expect(north.z).toBeCloseTo(0)
  })

  it('normalises a negative heading and drops −0', () => {
    const p = placementFromMatrix(matrixFor({ x: 0, y: 0, heading: -90 }))
    expect(p.heading).toBe(270)
    expect(Object.is(p.x, -0)).toBe(false)
  })
})
