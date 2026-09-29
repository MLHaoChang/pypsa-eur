// Hero models in the scene (Phase 2 spec §5, E8; plan Tasks 3.2, 3.4, 3.5).
// SiteCanvas-only: imports three.
//
// A model is loaded once per session by three's GLTFLoader with no Draco or
// meshopt decoder set — the models are uncompressed, and drei's useGLTF
// would by default fetch a Draco decoder from a Google CDN, which the
// offline desktop app must never do. Loads go through heroCache, never a
// suspending hook, so a model that fails to load leaves the parametric form
// without a page error. A model is drawn as one instanced mesh per mesh of
// the model, one instance per unit (heroes.ts decides the transforms). Each
// mesh's node transform is baked in; a mirroring root scale (the tank) is
// removed, since a negative determinant would turn the faces inside out.
// Materials are cloned per object, so a type's tint and one object's
// selection glow never leak onto every object using the same model; the
// cached geometry is shared and never disposed here.

import { useEffect, useLayoutEffect, useMemo, useRef, useSyncExternalStore } from 'react'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { createHeroCache, type HeroState } from './heroCache'
import { HERO_MODELS, heroInstances, heroUrl, type HeroModel } from './heroes'
import { toScene } from './scene'
import type { Part } from './templates'

const BASE = (import.meta.env.BASE_URL as string | undefined) ?? '/'

export const heroModelUrl = (m: HeroModel): string => heroUrl(m, BASE)

export interface Piece { geometry: THREE.BufferGeometry; material: THREE.Material; matrix: THREE.Matrix4; rotor: boolean }

/** A loaded model's meshes, each with its node transform baked in (mirroring removed) and rotor pieces marked. */
export function heroPieces(root: THREE.Object3D, m: HeroModel): Piece[] {
  root.updateMatrixWorld(true)
  const unmirror = new THREE.Matrix4().makeScale(-1, 1, 1)
  const pieces: Piece[] = []
  root.traverse(o => {
    const mesh = o as THREE.Mesh
    if (!mesh.isMesh) return
    const matrix = mesh.matrixWorld.clone()
    if (matrix.determinant() < 0) matrix.premultiply(unmirror)
    let rotor = false
    for (let p: THREE.Object3D | null = mesh; p && p !== root; p = p.parent) if (p.name === m.rotor?.node) rotor = true
    pieces.push({ geometry: mesh.geometry, material: mesh.material as THREE.Material, matrix, rotor })
  })
  return pieces
}

const byUrl = new Map(Object.values(HERO_MODELS).map(m => [heroModelUrl(m), m]))
const cache = createHeroCache<Piece[]>(async url => {
  const gltf = await new GLTFLoader().loadAsync(url)
  return heroPieces(gltf.scene, byUrl.get(url)!)
})

/** Start loading every hero (called when a site opens). */
export function preloadHeroes(): void {
  for (const url of byUrl.keys()) cache.load(url)
}

const NO_HERO: HeroState<Piece[]> = { status: 'idle' }

/** The model's load state (idle for no model); starts the load if needed. */
export function useHero(m: HeroModel | null): HeroState<Piece[]> {
  const url = m ? heroModelUrl(m) : null
  useEffect(() => { if (url) cache.load(url) }, [url])
  return useSyncExternalStore(cache.subscribe, () => (url ? cache.get(url) : NO_HERO))
}

const Y = new THREE.Vector3(0, 1, 0)
const X = new THREE.Vector3(1, 0, 0)

/** instance · T(centre-shifted hub) · R_x(angle) · T(−hub): a turbine's blades, spun about its own hub (WP6 animates `angle`). */
export function rotorMatrix(inst: THREE.Matrix4, m: HeroModel, angle: number): THREE.Matrix4 {
  const hub = m.rotor!.hub
  const toHub = new THREE.Matrix4().makeTranslation(hub[0], hub[1], hub[2])
  const back = new THREE.Matrix4().makeTranslation(-hub[0], -hub[1], -hub[2])
  const spin = new THREE.Matrix4().makeRotationAxis(X, angle)
  return inst.clone().multiply(toHub).multiply(spin).multiply(back)
}

export interface HeroInstancesProps {
  parts: Part[]
  /** The loaded model's pieces (useHero's 'ready' value). */
  pieces: Piece[]
  model: HeroModel
  /** The type's colour: the model's texture is lightly tinted with it. */
  tint: string
  glow: { color: string; intensity: number }
}

export function HeroInstances({ parts, pieces, model, tint, glow }: HeroInstancesProps) {
  const instances = useMemo(() => heroInstances(parts, model), [parts, model])
  // instance matrices, model → object frame: T(pos) · R_y(yaw) · S(scale) · T(centre)
  const bases = useMemo(() => instances.map(i => new THREE.Matrix4()
    .makeTranslation(...toScene(i.pos[0], i.pos[1], i.pos[2]))
    .multiply(new THREE.Matrix4().makeRotationAxis(Y, i.yaw))
    .multiply(new THREE.Matrix4().makeScale(...i.scale))
    .multiply(new THREE.Matrix4().makeTranslation(...i.centre))), [instances])
  const materials = useMemo(() => pieces.map(p => {
    const mat = (p.material as THREE.MeshStandardMaterial).clone()
    mat.color = new THREE.Color('#ffffff').lerp(new THREE.Color(tint), 0.35)
    return mat
  }), [pieces, tint])
  useEffect(() => () => { for (const m of materials) m.dispose() }, [materials])
  useLayoutEffect(() => {
    for (const m of materials) {
      m.emissive = new THREE.Color(glow.color)
      m.emissiveIntensity = glow.intensity
    }
  }, [materials, glow.color, glow.intensity])

  const refs = useRef<(THREE.InstancedMesh | null)[]>([])
  useLayoutEffect(() => {
    pieces.forEach((p, k) => {
      const mesh = refs.current[k]
      if (!mesh) return
      bases.forEach((b, i) => mesh.setMatrixAt(i, (p.rotor ? rotorMatrix(b, model, 0) : b.clone()).multiply(p.matrix)))
      mesh.instanceMatrix.needsUpdate = true
      mesh.computeBoundingBox()
      mesh.computeBoundingSphere()
    })
  }, [pieces, bases, model])

  if (!instances.length) return null
  return (
    <>
      {pieces.map((p, k) => (
        <instancedMesh
          // Keyed on the count: an InstancedMesh's capacity is fixed at construction.
          key={`${k}:${instances.length}`}
          name={p.rotor ? 'hero-rotor' : 'hero'}
          ref={el => { refs.current[k] = el }}
          args={[p.geometry, materials[k], instances.length]}
          castShadow
          receiveShadow
        />
      ))}
    </>
  )
}
