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
import { siteVisuals, type Visual } from './resultStyle'
import { anchorsSignature, chevronPath, gaugeBar, resultGlow } from './resultsDecor'
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

/** Bumped on every (re)registration, so per-frame caches notice an object's new anchors or colour. */
const versions = new WeakMap<ObjectRegistry, number>()
const bump = (r: ObjectRegistry) => versions.set(r, (versions.get(r) ?? 0) + 1)
export const registryVersion = (r: ObjectRegistry) => versions.get(r) ?? 0

/** Register an object's group while mounted (SiteObjectMesh). */
export function useRegisterObject(key: string, group: React.RefObject<THREE.Group | null>, obj: RegisteredObject['obj'], locked: boolean): void {
  const registry = useContext(ObjectRegistryContext)
  useLayoutEffect(() => {
    const g = group.current
    if (!registry || !g) return
    registry.set(key, { group: g, obj, locked }); bump(registry)
    return () => { if (registry.get(key)?.group === g) { registry.delete(key); bump(registry) } }
  }, [registry, key, group, obj, locked])
}

// ── Decorations ─────────────────────────────────────────────────────────────

const DECOR = 'site3dDecor'
const CHEVRONS = 4
const GAUGE_TRACK = '#1f2937'

interface Decor {
  sig: string
  gauge: THREE.Group | null
  gaugeFill: THREE.Mesh<THREE.BoxGeometry, THREE.MeshStandardMaterial> | null
  gaugeWidth: number
  gaugeColor: string
  flow: THREE.Group | null
  chevrons: THREE.Mesh<THREE.ShapeGeometry, THREE.MeshStandardMaterial>[]
  chevronColor: string
  from: [number, number, number]
  to: [number, number, number]
}

const mark = <T extends THREE.Object3D>(o: T): T => { o.traverse(c => { c.userData[DECOR] = true }); return o }

/** A flat arrowhead lying in the ground plane, pointing along +x (read from above and from any side, also standing still). */
function arrowhead(size: number): THREE.ShapeGeometry {
  const sh = new THREE.Shape()
  sh.moveTo(size, 0); sh.lineTo(-size * 0.6, size * 0.8); sh.lineTo(-size * 0.2, 0); sh.lineTo(-size * 0.6, -size * 0.8); sh.closePath()
  const g = new THREE.ShapeGeometry(sh)
  g.rotateX(-Math.PI / 2)            // shape XY → ground XZ, arrow along +x
  return g
}

function makeDecor(group: THREE.Group, anchors: Anchors | undefined): Decor {
  const d: Decor = { sig: anchorsSignature(anchors), gauge: null, gaugeFill: null, gaugeWidth: 0, gaugeColor: '', flow: null, chevrons: [], chevronColor: '', from: [0, 0, 0], to: [0, 0, 0] }
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
    d.from = toScene(...flow.from); d.to = toScene(...flow.to)
    const g = new THREE.Group(); g.name = 'flow'; g.visible = false
    const len = Math.hypot(d.to[0] - d.from[0], d.to[1] - d.from[1], d.to[2] - d.from[2])
    const geom = arrowhead(Math.max(0.6, len / 10))
    for (let k = 0; k < CHEVRONS; k++) {
      const c = new THREE.Mesh(k === 0 ? geom : geom.clone(), new THREE.MeshStandardMaterial({ color: '#16a34a', emissive: '#16a34a', emissiveIntensity: 0.5, side: THREE.DoubleSide }))
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
  /** The glow the layer last applied (null: selection / hover / outside own it). */
  glow: { color: string; intensity: number } | null
}
const fresh = (): EasedVisual => ({ fill: 0, emissive: 0, rpm: 0, angle: 0, phase: 0, gauge: false, flow: 0, loading: null, color: null, glow: null })

const tmpColor = new THREE.Color()
/** Re-scan an object's materials this often (a hero model arriving adds some). */
const RESCAN_FRAMES = 30

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

/** Rotor groups and hero blade setters under a turbine object. */
function rotorTargets(group: THREE.Group): { groups: THREE.Object3D[]; setters: ((a: number) => void)[] } {
  const groups: THREE.Object3D[] = [], setters: ((a: number) => void)[] = []
  group.traverse(o => {
    if (o.name.startsWith('rotor:')) groups.push(o)
    const set = o.userData.setRotorAngle as ((a: number) => void) | undefined
    if (set) setters.push(set)
  })
  return { groups, setters }
}

interface Scan { frame: number; materials: THREE.MeshStandardMaterial[]; rotors: ReturnType<typeof rotorTargets> | null }

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
  const scans = useRef(new Map<string, Scan>())
  const frame = useRef(0)
  const cache = useRef<{ r: SiteResults | null; version: number; visuals: Map<string, Visual> }>({ r: null, version: -1, visuals: new Map() })

  useEffect(() => {
    const decorations = decor.current
    return () => { for (const { group, d } of decorations.values()) disposeDecor(group, d); decorations.clear() }
  }, [])

  useFrame((_, deltaS) => {
    const dt = Math.min(deltaS * 1000, 1000)
    const n = ++frame.current
    const r = store.get()
    const version = registryVersion(registry)
    if (cache.current.r !== r || cache.current.version !== version) {
      cache.current = { r, version, visuals: siteVisuals(r.states, [...registry.values()].map(e => e.obj)) }
    }
    const visuals = cache.current.visuals

    // Decorations follow the registry: an object gone, remounted (a new
    // group) or resized (new anchors) loses its old ones.
    for (const [key, { group, d }] of decor.current) {
      const e = registry.get(key)
      if (!e || e.group !== group || anchorsSignature(e.obj.anchors) !== d.sig) { disposeDecor(group, d); decor.current.delete(key) }
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
      let scan = scans.current.get(key)
      if (!scan || n - scan.frame >= RESCAN_FRAMES || scan.materials.length === 0) {
        scan = { frame: n, materials: glowMaterials(e.group), rotors: e.obj.anchors?.rotors?.length ? rotorTargets(e.group) : null }
        scans.current.set(key, scan)
      }

      // Fill gauge.
      s.fill = easeTowards(s.fill, v?.fill ?? 0, dt, motion.tauMs)
      s.gauge = v?.fill != null && !!dec?.d.gauge
      if (dec?.d.gauge && dec.d.gaugeFill) {
        dec.d.gauge.visible = s.gauge
        if (s.gauge) {
          const bar = gaugeBar(s.fill, dec.d.gaugeWidth)
          dec.d.gaugeFill.scale.x = bar.scaleX
          dec.d.gaugeFill.position.x = bar.x
          if (v?.fillColor && v.fillColor !== dec.d.gaugeColor) {
            dec.d.gaugeColor = v.fillColor
            dec.d.gaugeFill.material.color.set(v.fillColor); dec.d.gaugeFill.material.emissive.set(v.fillColor)
          }
        }
      }

      // Glow: the result's, unless selection / hover / outside own it.
      s.emissive = easeTowards(s.emissive, v?.emissive ?? 0, dt, motion.tauMs)
      s.color = v?.color ?? null
      const glow = resultGlow(e.locked, v, s.emissive, e.obj.color)
      s.glow = glow
      if (glow) {
        tmpColor.set(glow.color)
        for (const m of scan.materials) {
          if (!m.emissive.equals(tmpColor)) m.emissive.copy(tmpColor)
          if (m.emissiveIntensity !== glow.intensity) m.emissiveIntensity = glow.intensity
        }
      }

      // Spin: parametric rotor groups and hero blade instances, per turbine
      // (the hero setters skip an unchanged angle).
      s.rpm = v?.spin ?? 0
      s.angle = (s.angle + rotorStep(s.rpm, dt, motion.spin)) % (2 * Math.PI)
      if (scan.rotors) {
        for (const o of scan.rotors.groups) o.rotation.z = s.angle + Number(o.name.slice(6)) * 0.7
        for (const set of scan.rotors.setters) set(s.angle)
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
          const path = chevronPath(dec.d.from, dec.d.to, s.flow, s.phase, CHEVRONS)
          const yaw = Math.atan2(-path.heading[2], path.heading[0])                // arrow along +x turned to the heading
          const color = v?.color ?? '#94a3b8'
          const recolour = color !== dec.d.chevronColor
          dec.d.chevronColor = color
          dec.d.chevrons.forEach((c, k) => {
            c.position.set(...path.points[k])
            c.rotation.set(0, yaw, 0)
            if (recolour) { c.material.color.set(color); c.material.emissive.set(color) }
          })
        }
      }
    }
    if (eased.current.size > registry.size) {
      for (const key of eased.current.keys()) if (!registry.has(key)) { eased.current.delete(key); scans.current.delete(key) }
    }
    if (debug) debug.current = eased.current
  })
  return null
}
