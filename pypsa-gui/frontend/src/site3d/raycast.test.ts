import { describe, it, expect } from 'vitest'
import { PerspectiveCamera, Vector3 } from 'three'
import { screenToGround, groundToScreen } from './raycast'

const rect = { left: 100, top: 50, width: 800, height: 600 } as DOMRect

function camera(): PerspectiveCamera {
  const c = new PerspectiveCamera(45, 800 / 600, 1, 10_000)
  c.position.set(0, 300, 400)        // south of the origin, looking north and down
  c.lookAt(new Vector3(0, 0, 0))
  c.updateMatrixWorld()
  c.updateProjectionMatrix()
  return c
}

describe('screen ↔ ground', () => {
  it('the screen centre hits the ground at the look-at point', () => {
    const g = screenToGround(camera(), rect, 100 + 400, 50 + 300)
    expect(g!.x).toBeCloseTo(0, 3)
    expect(g!.y).toBeCloseTo(0, 3)
  })

  it('round-trips a ground point through the screen', () => {
    const c = camera()
    const s = groundToScreen(c, rect, 60, -30)!
    const g = screenToGround(c, rect, s.x, s.y)!
    expect(g.x).toBeCloseTo(60, 3)
    expect(g.y).toBeCloseTo(-30, 3)
  })

  it('a ray pointing at the sky misses', () => {
    // A camera looking level: anything above the screen centre is sky.
    const level = new PerspectiveCamera(45, 800 / 600, 1, 10_000)
    level.position.set(0, 300, 400)
    level.lookAt(new Vector3(0, 300, 0))
    level.updateMatrixWorld(); level.updateProjectionMatrix()
    expect(screenToGround(level, rect, 100 + 400, 50 + 100)).toBeNull()
    expect(screenToGround(level, rect, 100 + 400, 50 + 500)).not.toBeNull()
  })

  it('a degenerate rect yields null', () => {
    expect(screenToGround(camera(), { left: 0, top: 0, width: 0, height: 0 } as DOMRect, 0, 0)).toBeNull()
  })
})
