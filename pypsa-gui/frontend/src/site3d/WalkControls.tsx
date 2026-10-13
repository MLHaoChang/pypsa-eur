// Walk the campus (visual-layers plan 3 S5): a first-person camera. drei's
// PointerLockControls turns the mouse into the look (click the canvas to
// grab the pointer; Escape releases it and leaves the mode), WASD / the
// arrows walk at a walking pace (walk.ts), the eye stays EYE_HEIGHT_M above
// the terrain under it (`heightAt`). Purely immersive: nothing is selected,
// moved or written here. No head bob, so reduced motion needs no special
// case. SiteCanvas-only (imports three); the orbit's camera pose is restored
// on exit so the user is back where they were.
import { useEffect, useRef } from 'react'
import { useFrame, useThree } from '@react-three/fiber'
import { PointerLockControls } from '@react-three/drei'
import * as THREE from 'three'
import type { PointerLockControls as PointerLockControlsImpl } from 'three-stdlib'
import type { HeightAt } from './context'
import { EYE_HEIGHT_M, WALK_KEYS, walkDelta, type WalkKey } from './walk'

interface Props {
  /** Where the walk starts, metres east/north of the site origin (the plot centre). */
  start: { x: number; y: number }
  heightAt: HeightAt
  /** The pointer was released (Escape, or focus lost): the caller leaves walk mode. */
  onExit: () => void
}

export default function WalkControls({ start, heightAt, onExit }: Props) {
  const camera = useThree(s => s.camera)
  const controls = useRef<PointerLockControlsImpl>(null)
  const held = useRef(new Set<WalkKey>())
  const dir = useRef(new THREE.Vector3())

  // Stand at the start, at eye height, facing north; keep the orbit's pose
  // to give back on exit. Grab the pointer at once (the Walk click is the
  // user gesture); if the browser refuses, a click on the canvas does it.
  useEffect(() => {
    const cam = camera
    const pose = { position: cam.position.clone(), quaternion: cam.quaternion.clone() }
    cam.position.set(start.x, heightAt(start.x, start.y) + EYE_HEIGHT_M, -start.y)
    cam.rotation.set(0, 0, 0, 'YXZ')
    try { controls.current?.lock() } catch { /* needs a click on the canvas */ }
    return () => { cam.position.copy(pose.position); cam.quaternion.copy(pose.quaternion) }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const down = (e: KeyboardEvent) => { const k = WALK_KEYS[e.code]; if (k) { held.current.add(k); e.preventDefault() } }
    const up = (e: KeyboardEvent) => { const k = WALK_KEYS[e.code]; if (k) held.current.delete(k) }
    const clear = () => held.current.clear()
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    window.addEventListener('blur', clear)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
      window.removeEventListener('blur', clear)
    }
  }, [])

  useFrame((_, dt) => {
    if (held.current.size === 0) return
    camera.getWorldDirection(dir.current)
    // Scene x is east, −z is north: the view on the ground is (x, −z).
    const step = walkDelta(held.current, { x: dir.current.x, y: -dir.current.z }, Math.min(dt, 0.1))
    const x = camera.position.x + step.x
    const north = -camera.position.z + step.y
    camera.position.set(x, heightAt(x, north) + EYE_HEIGHT_M, -north)
  })

  return <PointerLockControls ref={controls} onUnlock={onExit} />
}
