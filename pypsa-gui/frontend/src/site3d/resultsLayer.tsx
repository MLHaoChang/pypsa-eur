// Results in the scene (Phase 2 spec §6.4; plan Tasks 6.2, 6.5).
// SiteCanvas-only: imports three.
//
// The driver writes each snapshot's map into the results store; this layer
// reads it in useFrame and never re-renders for a snapshot. Per object it
// eases towards the snapshot's visual (motion.ts) and applies it to the
// object's registered group:
//  - fill: an exterior gauge (its own materials) along the fill anchor;
//  - glow: the result's emissive on the object's own materials (hero clones
//    included) — only while the object is not selected, hovered or outside
//    the boundary, which own the glow (precedence, resultStyle.emissiveFor);
//  - spin: each parametric rotor group, and each hero blade instance, about
//    its own hub;
//  - flow: chevrons along the flow anchor, owner side → far side (or back),
//    in the loading band's colour.
// Gauges and chevrons are added to the object's group imperatively and
// marked as decorations, so the glow skips them.
import { createContext, useContext, useEffect, useLayoutEffect, useMemo, useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import * as THREE from 'three'
import type { Anchors } from './templates'
import { toScene } from './scene'
import { emissiveFor, siteVisuals, type Visual } from './resultStyle'
import { easeTowards, motionFor, rotorStep, useReducedMotion } from './motion'
import type { ResultsStore } from './resultsStore'
import type { SiteResults } from './useSiteResults'

// ── Registry: object groups the layer drives ───────────────────────────────

export interface RegisteredObject {
  group: THREE.Group
  obj: { type: string; name: string; kind: string; bus: string; color: string; anchors?: Anchors }
  /** Selected, hovered or outside: that state owns the glow. */
  locked: boolean
}
export type ObjectRegistry = Map<string, RegisteredObject>

export const ObjectRegistryContext = createContext<ObjectRegistry | null>(null)

/** Register an object's group while mounted (SiteObjectMesh). */
export function useRegisterObject(key: string, group: React.RefObject<THREE.Group | null>, obj: RegisteredObject['obj'], locked: boolean): void {
  const registry = useContext(ObjectRegistryContext)
  useLayoutEffect(() => {
    const g = group.current
    if (!registry || !g) return
    registry.set(key, { group: g, obj, locked })
    return () => { if (registry.get(key)?.group === g) registry.delete(key) }
  }, [registry, key, group, obj, locked])
}

// ── Decorations ─────────────────────────────────────────────────────────────

const DECOR = 'site3dDecor'
const CHEVRONS = 4
const GAUGE_TRACK = '#1f2937'

interface Decor {
  gauge: THREE.Group | null
  gaugeFill: THREE.Mesh<THREE.BoxGeometry, THREE.MeshStandardMaterial> | null
  gaugeWidth: number
  flow: THREE.Group | null
  chevrons: THREE.Mesh<THREE.ConeGeometry, THREE.MeshStandardMaterial>[]
  from: THREE.Vector3
  to: THREE.Vector3
}

const mark = <T extends THREE.Object3D>(o: T): T => { o.userData[DECOR] = true; o.traverse(c => { c.userData[DECOR] = true }); return o }

function makeDecor(group: THREE.Group, anchors: Anchors | undefined): Decor {
  const d: Decor = { gauge: null, gaugeFill: null, gaugeWidth: 0, flow: null, chevrons: [], from: new THREE.Vector3(), to: new THREE.Vector3() }
  const fill = anchors?.fill
  if (fill) {
    const [w, depth, h] = fill.size
    const g = new THREE.Group(); g.name = 'gauge'; g.visible = false
    g.position.set(...toScene(fill.pos[0], fill.pos[1], fill.pos[2]))
    const track = new THREE.Mesh(new THREE.BoxGeometry(w, h, depth), new THREE.MeshStandardMaterial({ color: GAUGE_TRACK, transparent: true, opacity: 0.55 }))
    const bar = new THREE.Mesh(new THREE.BoxGeometry(w, h * 1.05, depth * 1.05), new THREE.MeshStandardMaterial({ color: '#16a34a', emissive: '#16a34a', emissiveIntensity: 0.35 }))
    g.add(track, bar)
    group.add(mark(g))
    d.gauge = g; d.gaugeFill = bar; d.gaugeWidth = w
  }
  const flow = anchors?.flow
  if (flow) {
    d.from.set(...toScene(...flow.from)); d.to.set(...toScene(...flow.to))
    const g = new THREE.Group(); g.name = 'flow'; g.visible = false
    const len = d.from.distanceTo(d.to)
    const r = Math.max(0.4, len / 14)
    for (let k = 0; k < CHEVRONS; k++) {
      const c = new THREE.Mesh(new THREE.ConeGeometry(r, r * 2, 8), new THREE.MeshStandardMaterial({ color: '#16a34a', emissive: '#16a34a', emissiveIntensity: 0.5 }))
      g.add(c); d.chevrons.push(c)
    }
    group.add(mark(g))
    d.flow = g
  }
  return d
}

function disposeDecor(group: THREE.Group, d: Decor): void {
  for (const o of [d.gauge, d.flow]) {
    if (!o) continue
    group.remove(o)
    o.traverse(c => { const m = c as THREE.Mesh; if (m.isMesh) { m.geometry.dispose(); (m.material as THREE.Material).dispose() } })
  }
}

// ── Per-object eased state ─────────────────────────────────────────────────

export interface EasedVisual {
  fill: number; emissive: number; rpm: number; angle: number; phase: number
  /** What the last frame showed (the debug hook reports it). */
  gauge: boolean; flow: -1 | 0 | 1; loading: number | null; color: string | null
}
const fresh = (): EasedVisual => ({ fill: 0, emissive: 0, rpm: 0, angle: 0, phase: 0, gauge: false, flow: 0, loading: null, color: null })

const UP = new THREE.Vector3(0, 1, 0)
const tmpColor = new THREE.Color()

/** Every glowable material under the group (decorations excluded). */
function glowMaterials(group: THREE.Group): THREE.MeshStandardMaterial[] {
  const out: THREE.MeshStandardMaterial[] = []
  group.traverse(o => {
    if (o.userData[DECOR]) return
    const m = o as THREE.Mesh
    if (!m.isMesh) return
    for (const mat of Array.isArray(m.material) ? m.material : [m.material]) {
      const s = mat as THREE.MeshStandardMaterial
      if (s.isMeshStandardMaterial) out.push(s)
    }
  })
  return out
}

export interface ResultsLayerProps {
  store: ResultsStore
  registry: ObjectRegistry
  /** Filled by the layer: the eased state per object, for the debug hook. */
  debug?: React.MutableRefObject<Map<string, EasedVisual>>
}

export function ResultsLayer({ store, registry, debug }: ResultsLayerProps) {
  const reduced = useReducedMotion()
  const motion = useMemo(() => motionFor(reduced), [reduced])
  const eased = useRef(new Map<string, EasedVisual>())
  const decor = useRef(new Map<string, { group: THREE.Group; d: Decor }>())
  const cache = useRef<{ r: SiteResults | null; objs: number; visuals: Map<string, Visual> }>({ r: null, objs: -1, visuals: new Map() })

  useEffect(() => {
    const decorations = decor.current
    return () => { for (const { group, d } of decorations.values()) disposeDecor(group, d); decorations.clear() }
  }, [])

  useFrame((_, deltaS) => {
    const dt = Math.min(deltaS * 1000, 1000)
    const r = store.get()
    if (cache.current.r !== r || cache.current.objs !== registry.size) {
      cache.current = { r, objs: registry.size, visuals: siteVisuals(r.states, [...registry.values()].map(e => e.obj)) }
    }
    const visuals = cache.current.visuals

    // Decorations follow the registry (an object remounted gets a new group).
    for (const [key, { group, d }] of decor.current) {
      const e = registry.get(key)
      if (!e || e.group !== group) { disposeDecor(group, d); decor.current.delete(key) }
    }

    for (const [key, e] of registry) {
      const v = visuals.get(key)
      let s = eased.current.get(key)
      if (!s) { s = fresh(); eased.current.set(key, s) }
      let dec = decor.current.get(key)
      if (!dec && (e.obj.anchors?.fill || e.obj.anchors?.flow)) {
        dec = { group: e.group, d: makeDecor(e.group, e.obj.anchors) }
        decor.current.set(key, dec)
      }

      // Fill gauge.
      s.fill = easeTowards(s.fill, v?.fill ?? 0, dt, motion.tauMs)
      s.gauge = v?.fill != null && !!dec?.d.gauge
      if (dec?.d.gauge && dec.d.gaugeFill) {
        dec.d.gauge.visible = s.gauge
        if (s.gauge) {
          const f = Math.max(0.001, s.fill)
          dec.d.gaugeFill.scale.x = f
          dec.d.gaugeFill.position.x = -dec.d.gaugeWidth / 2 + (dec.d.gaugeWidth * f) / 2
          if (v?.fillColor) { dec.d.gaugeFill.material.color.set(v.fillColor); dec.d.gaugeFill.material.emissive.set(v.fillColor) }
        }
      }

      // Glow: the result's, unless selection / hover / outside own it.
      s.emissive = easeTowards(s.emissive, v?.emissive ?? 0, dt, motion.tauMs)
      s.color = v?.color ?? null
      if (!e.locked) {
        const glow = v?.color
          ? { color: v.color, intensity: 0.35 }
          : emissiveFor({ selected: false, hovered: false, outside: false }, e.obj.color, v?.emissive != null ? s.emissive : undefined)
        tmpColor.set(glow.color)
        for (const m of glowMaterials(e.group)) {
          if (!m.emissive.equals(tmpColor)) m.emissive.copy(tmpColor)
          if (m.emissiveIntensity !== glow.intensity) m.emissiveIntensity = glow.intensity
        }
      }

      // Spin: parametric rotor groups and hero blade instances, per turbine.
      s.rpm = v?.spin ?? 0
      s.angle = (s.angle + rotorStep(s.rpm, dt, motion.spin)) % (2 * Math.PI)
      if (e.obj.anchors?.rotors?.length) {
        e.group.traverse(o => {
          if (o.name.startsWith('rotor:')) o.rotation.z = s!.angle + Number(o.name.slice(6)) * 0.7
          const set = o.userData.setRotorAngle as ((a: number) => void) | undefined
          if (set) set(s!.angle)
        })
      }

      // Flow chevrons.
      s.flow = v?.flow ? v.flow.dir : 0
      s.loading = v?.flow?.pct ?? null
      if (dec?.d.flow) {
        const show = s.flow !== 0
        dec.d.flow.visible = show
        if (show) {
          const speed = 0.25 + Math.min(1, (s.loading ?? 50) / 100) * 0.75       // laps per second along the segment
          if (motion.spin) s.phase = (s.phase + speed * dt / 1000) % 1
          const [a, b] = s.flow === 1 ? [dec.d.from, dec.d.to] : [dec.d.to, dec.d.from]
          const dir = new THREE.Vector3().subVectors(b, a).normalize()
          const q = new THREE.Quaternion().setFromUnitVectors(UP, dir)
          const color = v?.color ?? '#94a3b8'
          dec.d.chevrons.forEach((c, k) => {
            const t = (s!.phase + k / CHEVRONS) % 1
            c.position.lerpVectors(a, b, t)
            c.quaternion.copy(q)
            c.material.color.set(color); c.material.emissive.set(color)
          })
        }
      }
    }
    for (const key of [...eased.current.keys()]) if (!registry.has(key)) eased.current.delete(key)
    if (debug) debug.current = eased.current
  })
  return null
}
