// 3D site view.
//
// Phase 1 (docs/superpowers/specs/2026-09-29-3d-site-view-phase1-design.md):
// a SITE — a user-drawn boundary grouping one or more buses — opened as a
// portal from the map. One switchyard per member bus at that bus's offset
// from the site origin; every component attached to a member bus is drawn
// as parametric boxes packed around its yard, or at its placement if the
// user moved it; the boundary is a ribbon on the ground; the status line
// says whether the plan fits the plot (D9); the selected object carries a
// gizmo (D7).
//
// Clicking a box selects the component exactly as the other two canvases do
// (`setSelectedComponent({type, name})`), so the properties panel opens and
// an edit there re-generates the geometry through the normal query
// invalidation — the scene holds no state of its own beyond hover and camera.
//
// Frames. Everything in the scene is metres east/north of the SITE ORIGIN
// (the boundary's stored centroid); the layout is built in that frame.
//
// This module is loaded lazily (App.tsx) and is the only importer of three.js
// (and of the site3d modules that import it).

import React, { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Canvas, useFrame, useThree, type ThreeEvent } from '@react-three/fiber'
import { OrbitControls, Line, PivotControls } from '@react-three/drei'
import * as THREE from 'three'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import { networkApi } from '../api/network'
import { busLatLng } from '../utils/geo'
import { tileRangeAround, mosaicExtent, zoomFor, tileCount, fromLocal, type LngLat, type LocalExtent } from '../site3d/geo'
import { buildGroundMosaic, ESRI_ATTRIBUTION } from '../site3d/imagery'
import { sitesApi, contextErrorMessage, contextFailureBacksOff } from '../api/sites'
import {
  buildingsGeometry, linesToRibbons, areasGeometry, heightmapToDisplacement, terrainSampler, groundHeightAt,
  groundMode, hillshadeCanvas, terrainCellMetres, RIBBON_COLOR, AREA_COLOR, BUILDING_COLOR, OSM_ATTRIBUTION, type HeightAt,
} from '../site3d/context'
import { buildSiteLayout, objectKey, type SiteObject, type Part } from '../site3d/layout'
import { DEFAULT_LIBRARY, legendFor } from '../site3d/assetLibrary'
import { matrixFor, placementFromMatrix } from '../site3d/placementMath'
import { screenToGround, groundToScreen } from '../site3d/raycast'
import { registerSiteDropTarget, unregisterSiteDropTarget } from '../site3d/dropRegistry'
import SiteOverlay from '../components/SiteOverlay'
import { toScene, toBoxArgs, fitCamera, chooseSite, unionBounds, halfSizeFor, type Bounds } from '../site3d/scene'
import { useSitesStore } from '../site3d/sitesStore'
import { writeActiveSite } from '../site3d/activeSite'
import { boundaryToLocal } from '../site3d/boundary'
import { busOffsets, siteBounds } from '../site3d/siteModel'
import { fitReport, type FitObject } from '../site3d/fit'
import SiteEmptyState from '../components/SiteEmptyState'
import type { Bus } from '../api/types'
import type { Site, SiteContext } from '../site3d/types'

// ── One object = one group of boxes, one click target ────────────────────────

/**
 * One part's geometry. A box is sized (east, height, north) in scene axes; a
 * cylinder keeps the same size order (spec E4) and lies along its axis: three's
 * cylinder stands along scene Y (up), so a north-lying one is turned about X
 * and an east-lying one about Z.
 */
function PartGeometry({ part }: { part: Part }) {
  if (part.shape !== 'cylinder') return <boxGeometry args={toBoxArgs(part.size)} />
  const [e, n, h] = part.size
  if (part.axis === 'north') return <cylinderGeometry args={[e / 2, e / 2, n, 16]} onUpdate={g => g.rotateX(Math.PI / 2)} />
  if (part.axis === 'east') return <cylinderGeometry args={[n / 2, n / 2, e, 16]} onUpdate={g => g.rotateZ(Math.PI / 2)} />
  return <cylinderGeometry args={[e / 2, e / 2, h, 16]} />
}

function SiteObjectMesh({ obj, selected, hovered, outside, pivot, onHover, onSelect }: {
  obj: SiteObject
  selected: boolean
  hovered: boolean
  /** Sticks out of the site boundary (fit check, D9). */
  outside: boolean
  /** Wrapped in a PivotControls whose matrix carries position and heading — render at identity. */
  pivot?: boolean
  /** Called with the object's key (`Class:name`) on pointer-over, null on pointer-out. */
  onHover: (key: string | null) => void
  onSelect: (obj: SiteObject) => void
}) {
  countRender(`mesh:${obj.type}:${obj.name}`)
  const [ox, oy] = pivot ? [0, 0] : obj.origin
  const heading = pivot ? 0 : obj.heading
  const tint = selected ? '#ffffff' : hovered ? '#fde68a' : undefined
  const emissive = selected ? obj.color : outside ? '#dc2626' : '#000000'
  const emissiveIntensity = selected ? 0.6 : outside ? 0.45 : 0
  return (
    <group
      name={`${obj.type}:${obj.name}`}
      position={toScene(ox, oy, obj.elevation ?? 0)}
      // Heading is clockwise from north; a rotation about the up axis by −heading.
      rotation={[0, (-heading * Math.PI) / 180, 0]}
      onClick={(e: ThreeEvent<MouseEvent>) => { e.stopPropagation(); onSelect(obj) }}
      onPointerOver={(e: ThreeEvent<PointerEvent>) => { e.stopPropagation(); onHover(objectKey(obj)) }}
      onPointerOut={() => onHover(null)}
    >
      {obj.parts.map((p, i) => (
        <mesh
          key={i}
          position={toScene(p.pos[0], p.pos[1], p.pos[2])}
          rotation={[p.rotX ?? 0, p.rotZ ?? 0, -(p.rotN ?? 0)]}
          castShadow
          receiveShadow
        >
          <PartGeometry part={p} />
          <meshStandardMaterial
            color={tint ?? p.color ?? obj.color}
            emissive={emissive}
            emissiveIntensity={emissiveIntensity}
            roughness={0.7}
            metalness={0.1}
          />
        </mesh>
      ))}
    </group>
  )
}

// ── Hover / selection labels: plain DOM over the canvas ──────────────────────
//
// Not drei's <Html>: that renders each label through its own React root and
// unmounts it synchronously in a layout-effect cleanup, which React warns
// about when the 3D tree tears down mid-render (Open in 3D) and which
// occasionally threw "removeChild … not a child of this node" (Phase 1 QA).
// The labels are ordinary elements in the page's own tree; this component
// only moves them, once per frame, to the projected top of their object.

function LabelTracker({ keys, refs }: { keys: string[]; refs: React.MutableRefObject<Record<string, HTMLDivElement | null>> }) {
  const scene = useThree(s => s.scene)
  const camera = useThree(s => s.camera)
  const size = useThree(s => s.size)
  const box = useMemo(() => new THREE.Box3(), [])
  const v = useMemo(() => new THREE.Vector3(), [])
  useFrame(() => {
    for (const key of keys) {
      const el = refs.current[key]
      if (!el) continue
      const o = scene.getObjectByName(key)
      if (!o) { el.style.visibility = 'hidden'; continue }
      box.setFromObject(o)
      box.getCenter(v)
      v.y = box.max.y + 4
      v.project(camera)
      if (v.z > 1) { el.style.visibility = 'hidden'; continue }
      el.style.visibility = 'visible'
      el.style.transform = `translate(${((v.x + 1) / 2) * size.width}px, ${((1 - v.y) / 2) * size.height}px) translate(-50%, -100%)`
    }
  })
  return null
}

// ── Ground plane: the tile mosaic, or a flat grey slab while it loads ────────
//
// The imagery is UNLIT (meshBasicMaterial): a satellite photo already has the
// sun baked in, and shading it again darkens it and doubles the shadows. The
// objects' shadows still land on it through a second, transparent
// ShadowMaterial plane a hair above — three.js's standard shadow-catcher.

function Ground({ extent, texture, textureKind, geometry, onMiss }: {
  extent: LocalExtent
  texture: THREE.Texture | null
  /** Part of the material key: a hillshade and a photo must never share a compiled material. */
  textureKind: 'imagery' | 'hillshade'
  /** The terrain-displaced ground (WP5); a flat plane over `extent` when null. */
  geometry: THREE.BufferGeometry | null
  onMiss: () => void
}) {
  const w = extent.east - extent.west
  const d = extent.north - extent.south
  const cx = (extent.east + extent.west) / 2
  const cy = (extent.north + extent.south) / 2
  const flat: [number, number, number] = [-Math.PI / 2, 0, 0]
  /* Keyed so the textured material is a NEW material, not the grey one with
     `map` patched in: a material compiled without a map keeps its mapless
     program when `map` is assigned later, and under some drivers that
     renders black rather than white. */
  const material = texture
    ? <meshBasicMaterial key={textureKind} map={texture} toneMapped={false} />
    : <meshBasicMaterial key="flat" color="#6b7280" />
  if (geometry) {
    // Already in the scene frame (heights relative to the site origin); the
    // shadow catcher is the same surface a hair above, so shadows follow
    // the slope instead of floating over a hollow.
    return (
      <group>
        <mesh geometry={geometry} position={[0, -0.05, 0]} onClick={onMiss}>{material}</mesh>
        <mesh geometry={geometry} receiveShadow>
          <shadowMaterial transparent opacity={0.35} />
        </mesh>
      </group>
    )
  }
  return (
    <group>
      <mesh position={toScene(cx, cy, -0.05)} rotation={flat} onClick={onMiss}>
        <planeGeometry args={[w, d]} />
        {material}
      </mesh>
      <mesh position={toScene(cx, cy, 0)} rotation={flat} receiveShadow>
        <planeGeometry args={[w, d]} />
        <shadowMaterial transparent opacity={0.35} />
      </mesh>
    </group>
  )
}

// ── Site context: OSM footprints, lines and areas on the terrain (WP5) ───────
//
// Static scenery: no pointer handlers, so r3f never raycasts it and a click
// on a building counts as a miss (deselect), exactly like the ground.

function ContextScenery({ context, origin, heightAt }: { context: SiteContext; origin: LngLat; heightAt: HeightAt }) {
  // Drape to the terrain's cell size; with no terrain the ground is flat and nothing needs densifying.
  const cell = context.terrain ? terrainCellMetres(context.terrain) : Infinity
  const buildings = useMemo(() => buildingsGeometry(context.buildings, origin, heightAt), [context, origin, heightAt])
  const ribbons = useMemo(() => linesToRibbons(context.lines, origin, heightAt, cell), [context, origin, heightAt, cell])
  const areas = useMemo(() => areasGeometry(context.areas, origin, heightAt, cell), [context, origin, heightAt, cell])
  useEffect(() => () => {
    buildings.dispose()
    ribbons.forEach(r => r.geometry.dispose())
    areas.forEach(a => a.geometry.dispose())
  }, [buildings, ribbons, areas])
  return (
    <group name="site-context">
      {areas.map(a => (
        <mesh key={`area:${a.kind}`} geometry={a.geometry}>
          <meshStandardMaterial color={AREA_COLOR[a.kind]} roughness={a.kind === 'water' ? 0.35 : 0.9} metalness={0} />
        </mesh>
      ))}
      {ribbons.map(r => (
        <mesh key={`line:${r.kind}`} geometry={r.geometry} receiveShadow>
          <meshStandardMaterial color={RIBBON_COLOR[r.kind]} roughness={1} metalness={0} />
        </mesh>
      ))}
      {buildings.getAttribute('position')?.count > 0 && (
        <mesh geometry={buildings} castShadow receiveShadow>
          <meshStandardMaterial color={BUILDING_COLOR} roughness={0.9} metalness={0} />
        </mesh>
      )}
    </group>
  )
}

/** An upstream failure is not retried for this long: a rate-limited Overpass must not be hit on every view switch. Refresh bypasses it. */
const CONTEXT_RETRY_MS = 5 * 60_000
const contextFailures = new Map<string, { at: number; message: string }>()

/** One line for the status strip: what the context holds. */
function contextSummary(c: SiteContext): string {
  const parts = [`${c.buildings.length} building${c.buildings.length === 1 ? '' : 's'}`, `${c.lines.length} line${c.lines.length === 1 ? '' : 's'}`]
  if (c.terrain) parts.push(c.terrain.missing_tiles ? `terrain (${c.terrain.missing_tiles} tile${c.terrain.missing_tiles === 1 ? '' : 's'} missing)` : 'terrain')
  return parts.join(' · ')
}

// ── Boundary ribbon on the ground ────────────────────────────────────────────

function BoundaryRibbon({ site, heightAt }: { site: Site; heightAt: HeightAt }) {
  const points = useMemo(() => {
    const pts = boundaryToLocal(site.boundary, site.origin).map(p => toScene(p.x, p.y, heightAt(p.x, p.y) + 0.4))
    return [...pts, pts[0]]
  }, [site, heightAt])
  return <Line points={points} color="#b3261e" lineWidth={2} />
}

// ── Camera: fit the packed site once per mount (the Canvas is keyed on the site) ──

function FitCamera({ bounds }: { bounds: Bounds }) {
  const camera = useThree(s => s.camera)
  const controls = useThree(s => s.controls) as { target: THREE.Vector3; update: () => void } | null
  const size = useThree(s => s.size)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    const cam = camera as THREE.PerspectiveCamera
    const fit = fitCamera(bounds, size.width / Math.max(size.height, 1), cam.fov)
    cam.position.set(...fit.position)
    cam.far = fit.far
    cam.updateProjectionMatrix()
    cam.lookAt(...fit.target)
    if (controls) { controls.target.set(...fit.target); controls.update() }
  }, [controls])
  return null
}

// ── Debug hook (permanent, gated) — how a browser test drives this view ──────
//
// `page.screenshot` blanks large textured planes under SwiftShader (spike
// note), so a test reads the canvas through `snapshot()`; and it needs each
// object's screen position to click it. Only in dev builds, or when the URL
// carries `site3dDebug`.

const DEBUG_ENABLED = import.meta.env.DEV || (typeof location !== 'undefined' && location.search.includes('site3dDebug'))

/**
 * Render counters for the debug hook (Phase 2 plan Tasks 0.4, 2.2, 6.2): how
 * often each component has rendered, so a browser test can show that a
 * snapshot step re-renders only the results driver. Counted only when the
 * hook is enabled. They count render CALLS, not commits (StrictMode doubles
 * them in a dev build) and are never reset: compare deltas across an action,
 * never absolute values.
 */
const renderCounts: Record<string, number> = {}
function countRender(name: string): void {
  if (DEBUG_ENABLED) renderCounts[name] = (renderCounts[name] ?? 0) + 1
}

function Site3dDebugHook({ objects, site, context, groundMode: mode, heightAt }: { objects: SiteObject[]; site: Site; context: SiteContext | null; groundMode: string; heightAt: HeightAt }) {
  const camera = useThree(s => s.camera)
  const scene = useThree(s => s.scene)
  const gl = useThree(s => s.gl)
  const size = useThree(s => s.size)
  useEffect(() => {
    // From the object's DATA, not its meshes: the centre of its first part
    // (the main body by convention — a genset block's 20 m stack or a wind
    // object's empty sky between turbines would put a bounding-box centre in
    // mid-air), through the object's origin, heading and terrain lift. After
    // Phase 2 merges an object's parts into one mesh, or replaces them with a
    // hero model, the meshes no longer say where the body is.
    const project = (key: string) => {
      const obj = objects.find(o => objectKey(o) === key)
      const first = obj?.parts[0]
      if (!obj || !first) return null
      const local = new THREE.Vector3(...toScene(first.pos[0], first.pos[1], first.pos[2]))
        .applyAxisAngle(new THREE.Vector3(0, 1, 0), (-obj.heading * Math.PI) / 180)
      const world = local.add(new THREE.Vector3(...toScene(obj.origin[0], obj.origin[1], heightAt(obj.origin[0], obj.origin[1]) + (obj.elevation ?? 0))))
      camera.updateMatrixWorld()
      const v = world.project(camera)
      const rect = gl.domElement.getBoundingClientRect()
      return { x: rect.left + ((v.x + 1) / 2) * rect.width, y: rect.top + ((1 - v.y) / 2) * rect.height }
    }
    const hook = {
      project,
      objects: objects.map(o => ({ key: `${o.type}:${o.name}`, kind: o.kind, origin: o.origin, heading: o.heading, parts: o.parts.length, summary: o.summary })),
      /** Draw calls of the last frame (the shadow pass counts too). */
      calls: () => gl.info.render.calls,
      renders: () => ({ ...renderCounts }),
      site: { id: site.id, name: site.name, placements: site.placements },
      context: context ? { buildings: context.buildings.length, lines: context.lines.length, areas: context.areas.length, terrain: !!context.terrain, missingTiles: context.terrain?.missing_tiles ?? null } : null,
      groundMode: mode,
      snapshot: () => { gl.render(scene, camera); return gl.domElement.toDataURL('image/png') },
    }
    ;(window as unknown as { __site3d?: unknown }).__site3d = hook
    return () => { delete (window as unknown as { __site3d?: unknown }).__site3d }
  }, [camera, scene, gl, size, objects, site, context, mode, heightAt])
  return null
}

// ── Drop target: offer the ground plane to the palette drag (D16) ────────────

function DropTargetRegistrar({ siteId }: { siteId: string }) {
  const camera = useThree(s => s.camera)
  const gl = useThree(s => s.gl)
  useEffect(() => {
    const target = {
      siteId,
      screenToGround: (cx: number, cy: number) => screenToGround(camera, gl.domElement.getBoundingClientRect(), cx, cy),
      groundToScreen: (x: number, y: number) => groundToScreen(camera, gl.domElement.getBoundingClientRect(), x, y),
    }
    registerSiteDropTarget(target)
    return () => unregisterSiteDropTarget(target)
  }, [siteId, camera, gl])
  return null
}

// ── The selected object's gizmo ───────────────────────────────────────────────

function SelectedPivot({ obj, enabled, onCommit, children }: {
  obj: SiteObject
  enabled: boolean
  onCommit: (p: { x: number; y: number; heading: number }) => void
  children: React.ReactNode
}) {
  // A NEW matrix per position: drei's autoTransform writes into this object
  // during a drag; when the placement lands in the store the layout gives a
  // new origin and this memo hands the pivot a fresh, equal matrix.
  const matrix = useMemo(() => matrixFor({ x: obj.origin[0], y: obj.origin[1], heading: obj.heading }),
    [obj.origin[0], obj.origin[1], obj.heading]) // eslint-disable-line react-hooks/exhaustive-deps
  // drei's onDragEnd carries no matrix and fires on every pointer-up on a
  // handle, moved or not. The last dragged matrix is kept here, PER pivot
  // (keyed per object, so a fresh one per selection), seeded at drag start,
  // and a click that never moved commits nothing — a packed object must not
  // become "placed" by being touched.
  const last = useRef(new THREE.Matrix4())
  const moved = useRef(false)
  return (
    <PivotControls
      matrix={matrix}
      activeAxes={[true, false, true]}
      disableScaling
      enabled={enabled}
      fixed
      scale={90}
      depthTest={false}
      annotations={false}
      onDragStart={() => { last.current.copy(matrix); moved.current = false }}
      onDrag={l => { last.current.copy(l); moved.current = true }}
      onDragEnd={() => { if (moved.current) onCommit(placementFromMatrix(last.current)) }}
    >
      {children}
    </PivotControls>
  )
}

// ── The view ──────────────────────────────────────────────────────────────────

export default function SiteCanvas() {
  countRender('SiteCanvas')
  const { currentProject, selectedComponent, setSelectedComponent } = useUIStore()
  const activeSiteId = useUIStore(s => s.activeSiteId)
  const setActiveSiteId = useUIStore(s => s.setActiveSiteId)
  const sitesDoc = useSitesStore(s => s.docFor(currentProject))
  const setPlacement = useSitesStore(s => s.setPlacement)
  const removePlacement = useSitesStore(s => s.removePlacement)
  const arrangeAll = useSitesStore(s => s.arrangeAll)
  const readOnly = useUIStore(s => s.readOnly)

  const qBuses = useQuery({ queryKey: nk(currentProject, 'buses'), queryFn: networkApi.getBuses })
  const qGen   = useQuery({ queryKey: nk(currentProject, 'generators'),    queryFn: networkApi.getGenerators })
  const qSu    = useQuery({ queryKey: nk(currentProject, 'storage_units'), queryFn: networkApi.getStorageUnits })
  const qSt    = useQuery({ queryKey: nk(currentProject, 'stores'),        queryFn: networkApi.getStores })
  const qLoad  = useQuery({ queryKey: nk(currentProject, 'loads'),         queryFn: networkApi.getLoads })
  const qTr    = useQuery({ queryKey: nk(currentProject, 'transformers'),  queryFn: networkApi.getTransformers })
  const qLine  = useQuery({ queryKey: nk(currentProject, 'lines'),         queryFn: networkApi.getLines })
  const qLink  = useQuery({ queryKey: nk(currentProject, 'links'),         queryFn: networkApi.getLinks })
  const buses = qBuses.data ?? [], isLoading = qBuses.isLoading
  const generators = qGen.data ?? [], storageUnits = qSu.data ?? [], stores = qSt.data ?? [], loads = qLoad.data ?? []
  const transformers = qTr.data ?? [], lines = qLine.data ?? [], links = qLink.data ?? []
  // Arrange rewrites the placement map from the CURRENT layout; with a
  // component list still loading (or failed) it would write a partial site.
  const allLoaded = [qBuses, qGen, qSu, qSt, qLoad, qTr, qLine, qLink].every(q => q.isSuccess)

  // uiStore restores the remembered site on every project switch; here we
  // only clear an id whose site no longer exists (deleted elsewhere), so the
  // picker and the store never disagree.
  const sitesLoaded = useSitesStore(s => !!s.loaded[currentProject ?? '__local__'])
  useEffect(() => {
    if (sitesLoaded && activeSiteId && !sitesDoc.sites.some(x => x.id === activeSiteId)) {
      setActiveSiteId(null)
      writeActiveSite(currentProject, null)
    }
  }, [sitesLoaded, activeSiteId, sitesDoc, currentProject, setActiveSiteId])

  const selectedBus = selectedComponent?.type === 'Bus' ? selectedComponent.name : null
  const site = useMemo(() => chooseSite(sitesDoc, activeSiteId, selectedBus), [sitesDoc, activeSiteId, selectedBus])

  // The packed layout for one bus alone, in that bus's frame — what the
  // empty state needs to size a default boundary.
  // Every network bus's carrier (not only the site's): the asset library
  // tells an electrolyser from a fuel cell by the far bus's carrier, and a
  // switchyard from an H₂/heat manifold by the bus's own.
  const busCarrier = useMemo(() => {
    const byName = new Map((buses as Bus[]).map(b => [b.name, b.carrier]))
    return (name: string) => byName.get(name)
  }, [buses])

  const layoutForBus = (busName: string) => {
    const bus = buses.find(b => b.name === busName)
    return buildSiteLayout({ buses: [{ name: busName, v_nom: bus?.v_nom ?? 0, offset: [0, 0] }], generators, storageUnits, stores, loads, transformers, lines, links, busCarrier })
  }

  // Member buses with a position; a member that is not placed cannot be
  // drawn and is reported on the status line.
  const offsets = useMemo(() => (site ? busOffsets(site, buses as Bus[]) : {}), [site, buses])
  const memberInputs = useMemo(() => (site ? site.buses
    .filter(name => offsets[name] != null)
    .map(name => ({ name, v_nom: buses.find(b => b.name === name)?.v_nom ?? 0, offset: offsets[name] })) : []),
  [site, offsets, buses])
  const unplacedMembers = site ? site.buses.filter(name => offsets[name] == null) : []

  // The layout is in the SITE frame already: one yard per bus at its
  // offset, placements honoured.
  const layout = useMemo(() => (site && memberInputs.length > 0
    ? buildSiteLayout({ buses: memberInputs, generators, storageUnits, stores, loads, transformers, lines, links, placements: site.placements, busCarrier })
    : null),
  [site, memberInputs, generators, storageUnits, stores, loads, transformers, lines, links, busCarrier])
  const objects: SiteObject[] = layout?.objects ?? []

  // ── Site context (WP5): the cache first (never an upstream call), then
  // one fetch when online; a failure is shown, not retried in a loop. ──────
  const [context, setContext] = useState<SiteContext | null>(null)
  const [contextStatus, setContextStatus] = useState<string>('')
  const [contextBusy, setContextBusy] = useState(false)
  // Refresh: bump the nonce with the force flag set; the effect then skips
  // the cache and the backoff and re-POSTs (the cache is replaced only when
  // the new fetch succeeds, so a failed refresh keeps what was there).
  const [contextNonce, setContextNonce] = useState(0)
  const forceContextRef = useRef(false)
  const siteId = site?.id ?? null
  useEffect(() => {
    const force = forceContextRef.current
    forceContextRef.current = false
    if (!force) setContext(null)
    if (!siteId) { setContextStatus(''); return }
    if (!currentProject) { setContextStatus('site context needs a saved project'); return }
    let cancelled = false
    const key = `${currentProject}/${siteId}`
    setContextStatus(force ? 'refreshing site context…' : 'reading site context…')
    setContextBusy(true)
    ;(async () => {
      try {
        let doc = force ? null : await sitesApi.getContext(currentProject, siteId)
        if (!doc) {
          if (typeof navigator !== 'undefined' && navigator.onLine === false) {
            if (!cancelled) setContextStatus('offline: no cached site context')
            return
          }
          const failed = contextFailures.get(key)
          if (!force && failed && Date.now() - failed.at < CONTEXT_RETRY_MS) {
            if (!cancelled) setContextStatus(`site context unavailable: ${failed.message}`)
            return
          }
          // Checked before the upstream call too: a StrictMode double-mount
          // or a quick site switch must not spend two Overpass calls.
          if (cancelled) return
          setContextStatus('fetching site context (OpenStreetMap + terrain)…')
          doc = await sitesApi.fetchContext(currentProject, siteId)
          contextFailures.delete(key)
        }
        if (cancelled) return
        setContext(doc)
        setContextStatus(contextSummary(doc))
      } catch (err: unknown) {
        const message = contextErrorMessage(err)
        if (contextFailureBacksOff(err)) contextFailures.set(key, { at: Date.now(), message })
        if (!cancelled) setContextStatus(`site context unavailable: ${message}`)
      } finally {
        if (!cancelled) setContextBusy(false)
      }
    })()
    return () => { cancelled = true }
  }, [currentProject, siteId, contextNonce])

  // Heights relative to the site origin: the origin stays at y = 0 (the
  // packed layout, placements and the drop plane all live there) and the
  // rest of the ground rises and falls around it.
  const terrain = context?.terrain ?? null
  const siteOrigin: LngLat | null = site ? site.origin : null
  const baseHeight = useMemo(() => (terrain && siteOrigin ? groundHeightAt(terrain, siteOrigin) : 0), [terrain, siteOrigin])
  const heightAt: HeightAt = useMemo(() => (terrain && siteOrigin ? terrainSampler(terrain, siteOrigin, baseHeight) : () => 0), [terrain, siteOrigin, baseHeight])

  // Two extents. The CAMERA frames the plot (spec §6.2: fit to the boundary,
  // not to the assets — a wind farm packed outside the fence must not shrink
  // the site to a speck); the ground mosaic and the shadow frustum cover the
  // union, so an asset outside the fence still stands on imagery.
  const plotExtent: Bounds | null = useMemo(() => (site ? siteBounds(site) : null), [site])
  const siteExtent: Bounds | null = useMemo(() => {
    if (!site || !layout) return null
    return unionBounds(siteBounds(site), layout.bounds)
  }, [site, layout])
  const halfSizeM = siteExtent ? halfSizeFor(siteExtent) : 0

  const fit = useMemo(() => {
    if (!site) return null
    const fo: FitObject[] = objects.map(o => ({ key: `${o.type}:${o.name}`, origin: o.origin, footprint: o.footprint, heading: o.heading, areaM2: o.areaM2 }))
    return fitReport(fo, site)
  }, [objects, site])
  const outsideSet = useMemo(() => new Set(fit?.outside ?? []), [fit])

  // Ground: tile range around the SITE origin sized to the extent, stitched
  // once per (site, size). Keyed on the quantised half-size, not the layout
  // object: a parameter edit rebuilds the layout every time, and refetching
  // 36 tiles because a genset gained an enclosure is the one thing this
  // must not do.
  const origin: LngLat | null = site ? site.origin : null
  const ground = useMemo(() => {
    if (!origin || !halfSizeM) return null
    const z = zoomFor(origin.lat, halfSizeM)
    const range = tileRangeAround(origin, halfSizeM, z)
    return { range, extent: mosaicExtent(origin, range), z }
  }, [origin, halfSizeM])

  const [texture, setTexture] = useState<THREE.CanvasTexture | null>(null)
  const [tileStatus, setTileStatus] = useState<string>('')
  // 'failed' when the mosaic rejected OR not one tile arrived (offline):
  // the ground then falls back to the terrain hillshade (Task 5.6).
  const [imageryState, setImageryState] = useState<'loading' | 'ok' | 'failed'>('loading')
  useEffect(() => {
    if (!ground) return
    let cancelled = false
    setTexture(null)
    setImageryState('loading')
    setTileStatus(`fetching ${tileCount(ground.range)} tiles at z${ground.z}…`)
    buildGroundMosaic(ground.range).then(({ canvas, missing }) => {
      if (cancelled) return
      if (missing >= tileCount(ground.range)) {
        setImageryState('failed')
        setTileStatus('imagery unavailable: no tile loaded')
        return
      }
      setImageryState('ok')
      const t = new THREE.CanvasTexture(canvas)
      t.colorSpace = THREE.SRGBColorSpace
      t.anisotropy = 8
      setTexture(t)
      setTileStatus(`${tileCount(ground.range)} tiles at z${ground.z}${missing ? ` · ${missing} missing` : ''}`)
    }).catch((err: unknown) => {
      if (cancelled) return
      setImageryState('failed')
      setTileStatus(`imagery unavailable: ${err instanceof Error ? err.message : String(err)}`)
    })
    return () => { cancelled = true }
  }, [ground])
  useEffect(() => () => { texture?.dispose() }, [texture])
  const imageryFailed = imageryState === 'failed'

  // The ground surface follows the terrain once the context is in.
  const groundGeometry = useMemo(() => (terrain && siteOrigin && ground
    ? heightmapToDisplacement(terrain, siteOrigin, ground.extent, baseHeight)
    : null), [terrain, siteOrigin, ground, baseHeight])
  useEffect(() => () => { groundGeometry?.dispose() }, [groundGeometry])

  // Offline degrade (Task 5.6): no imagery → a hillshade of the cached
  // terrain, mapped onto the ground extent through the texture transform
  // (the grid spans the padded boundary bbox, the ground the union extent;
  // clamped at the edges like groundHeightAt).
  const mode = groundMode(context, !imageryFailed)
  const hillshadeTexture = useMemo(() => {
    if (mode !== 'hillshade' || !terrain || !siteOrigin || !ground || typeof document === 'undefined') return null
    const t = new THREE.CanvasTexture(hillshadeCanvas(terrain))
    const [minLng, minLat, maxLng, maxLat] = terrain.bbox
    const sw = fromLocal(siteOrigin, { x: ground.extent.west, y: ground.extent.south })
    const ne = fromLocal(siteOrigin, { x: ground.extent.east, y: ground.extent.north })
    t.wrapS = THREE.ClampToEdgeWrapping
    t.wrapT = THREE.ClampToEdgeWrapping
    t.offset.set((sw.lng - minLng) / (maxLng - minLng || 1), (sw.lat - minLat) / (maxLat - minLat || 1))
    t.repeat.set((ne.lng - sw.lng) / (maxLng - minLng || 1), (ne.lat - sw.lat) / (maxLat - minLat || 1))
    t.colorSpace = THREE.SRGBColorSpace
    return t
  }, [mode, terrain, siteOrigin, ground])
  useEffect(() => () => { hillshadeTexture?.dispose() }, [hillshadeTexture])
  const groundTexture = mode === 'imagery' ? texture : mode === 'hillshade' ? hillshadeTexture : null

  const [hovered, setHovered] = useState<string | null>(null)
  const labelRefs = useRef<Record<string, HTMLDivElement | null>>({})

  // ── Empty states ──────────────────────────────────────────────────────────
  if (isLoading) {
    return <div className="flex h-full items-center justify-center text-[12px] text-muted">Loading network…</div>
  }
  if (!site) {
    return <SiteEmptyState buses={buses as Bus[]} boundsFor={name => layoutForBus(name).bounds} />
  }
  if (!layout || !ground || !siteExtent || !plotExtent || !fit) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-center px-8">
        <div className="text-[13px] font-medium text-text">{site.name} has no placed bus</div>
        <div className="text-[12px] text-muted max-w-md">Add a placed bus to this site (Satellite view → click the boundary → Edit buses).</div>
      </div>
    )
  }

  const legend = legendFor(objects)
  const selectedName = selectedComponent?.name ?? null
  const selectedType = selectedComponent?.type ?? null
  const selectedKey = selectedType && selectedName ? `${selectedType}:${selectedName}` : null
  const selectedObj = objects.find(o => objectKey(o) === selectedKey) ?? null
  const selectedPlacedKey = selectedKey && layout.placed.includes(selectedKey) ? selectedKey : null
  const labelObjects = objects.filter(o => { const k = objectKey(o); return k === selectedKey || k === hovered })

  return (
    <div className="relative h-full w-full bg-canvas">
      {/* key: a new site gets a fresh camera; a parameter edit does not. */}
      <Canvas
        key={site.id}
        className="site3d-canvas"
        shadows
        dpr={[1, 2]}
        camera={{ fov: 45, near: 1, far: 50_000 }}
        onPointerMissed={() => setSelectedComponent(null)}
      >
        <color attach="background" args={['#cfd8e3']} />
        <hemisphereLight args={['#ffffff', '#8a9bb0', 0.6]} />
        <directionalLight
          position={toScene(-halfSizeM, -halfSizeM * 0.6, halfSizeM * 1.4)}
          intensity={1.6}
          castShadow
          shadow-mapSize={[2048, 2048]}
          shadow-camera-left={-halfSizeM}
          shadow-camera-right={halfSizeM}
          shadow-camera-top={halfSizeM}
          shadow-camera-bottom={-halfSizeM}
          shadow-camera-near={1}
          shadow-camera-far={halfSizeM * 5}
          shadow-normalBias={1.5}
          shadow-bias={-0.0002}
        />
        <Ground
          extent={ground.extent}
          texture={groundTexture}
          textureKind={mode === 'hillshade' ? 'hillshade' : 'imagery'}
          geometry={groundGeometry}
          onMiss={() => setSelectedComponent(null)}
        />
        {context && <ContextScenery context={context} origin={site.origin} heightAt={heightAt} />}
        <BoundaryRibbon site={site} heightAt={heightAt} />
        {objects.map(o => {
          const key = objectKey(o)
          const isSelected = key === selectedKey
          const mesh = (
            <SiteObjectMesh
              key={key}
              obj={o}
              selected={isSelected}
              hovered={key === hovered}
              outside={outsideSet.has(key)}
              pivot={isSelected}
              onHover={setHovered}
              onSelect={obj => setSelectedComponent({ type: obj.type, name: obj.name })}
            />
          )
          // Each object stands at the ground height under its origin; the
          // lift is a translation-only parent, so the pivot's LOCAL matrix
          // still holds exactly the placement (x, heading, z).
          const lift = heightAt(o.origin[0], o.origin[1])
          if (!isSelected) return <group key={`lift:${key}`} position={[0, lift, 0]}>{mesh}</group>
          // The selected object carries the gizmo (D7): translate in the
          // ground plane, rotate about the vertical, no scaling.
          return (
            <group key={`lift:${key}`} position={[0, lift, 0]}>
              <SelectedPivot obj={o} enabled={!readOnly}
                onCommit={p => setPlacement(currentProject, site.id, key, p)}>
                {mesh}
              </SelectedPivot>
            </group>
          )
        })}
        <OrbitControls
          makeDefault
          maxPolarAngle={Math.PI / 2 - 0.05}
          minDistance={20}
          maxDistance={halfSizeM * 8}
          enableDamping
        />
        <FitCamera bounds={plotExtent} />
        <DropTargetRegistrar siteId={site.id} />
        <LabelTracker keys={labelObjects.map(objectKey)} refs={labelRefs} />
        {DEBUG_ENABLED && <Site3dDebugHook objects={objects} site={site} context={context} groundMode={mode} heightAt={heightAt} />}
      </Canvas>

      {/* Hover / selection labels (LabelTracker positions them every frame). */}
      <div className="pointer-events-none absolute inset-0 z-[300] overflow-hidden">
        {labelObjects.map(o => {
          const key = objectKey(o)
          return (
            <div key={key} ref={el => { labelRefs.current[key] = el }} data-testid="site-label"
              className="absolute left-0 top-0 whitespace-nowrap rounded bg-bg/95 border border-border px-2 py-1 text-[11px] text-text shadow"
              style={{ visibility: 'hidden' }}>
              <div className="font-semibold">{o.name}{outsideSet.has(key) ? <span className="ml-2 text-[10px] text-accent">outside the boundary</span> : null}</div>
              <div className="text-muted">{o.summary}</div>
            </div>
          )
        })}
      </div>

      <SiteOverlay
        sites={sitesDoc.sites}
        site={site}
        onPickSite={id => { setActiveSiteId(id); writeActiveSite(currentProject, id) }}
        assetCount={objects.filter(o => !DEFAULT_LIBRARY.find(t => t.id === o.kind)?.flags?.infrastructure).length}
        fit={fit}
        unplacedMembers={unplacedMembers}
        selectedPlacedKey={selectedPlacedKey}
        canArrange={allLoaded}
        onArrange={() => arrangeAll(currentProject, site.id, objects.map(o => ({ key: objectKey(o), origin: o.origin, heading: o.heading })), layout.orphans)}
        onResetPlacement={() => { if (selectedPlacedKey) removePlacement(currentProject, site.id, selectedPlacedKey) }}
      />

      {/* Legend — under the picker. */}
      <div className="absolute left-3 top-[5.5rem] z-[400] flex flex-wrap gap-x-3 gap-y-1 rounded-md border border-border bg-bg/95 px-2 py-1.5 text-[11px] shadow max-w-[60%]">
        {legend.map(e => (
          <span key={e.id} className="flex items-center gap-1 text-muted">
            <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: e.color }} />
            {e.label}
          </span>
        ))}
      </div>

      {/* Attribution — bottom-right above the snapshot bar: required for the
          Esri tiles, and ODbL requires the OSM credit whenever its data is
          drawn (the context scenery). */}
      <div className="absolute right-3 bottom-12 z-[400] max-w-[45%] rounded bg-bg/80 px-1.5 py-0.5 text-right text-[10px] text-muted">
        {tileStatus ? `${tileStatus} · ` : ''}
        {contextStatus ? <span data-testid="context-status">{contextStatus} · </span> : null}
        {currentProject && (
          <>
            <button
              type="button"
              data-testid="context-refresh"
              className="underline decoration-dotted hover:text-text disabled:no-underline disabled:opacity-50"
              disabled={readOnly || contextBusy}
              title={readOnly ? 'Read-only: the site context cannot be refetched now' : 'Fetch the OpenStreetMap buildings, lines and terrain for this site again'}
              onClick={() => { forceContextRef.current = true; setContextNonce(n => n + 1) }}
            >
              Refresh context
            </button>
            {' · '}
          </>
        )}
        {/* The OSM credit is not taken from the document: ODbL requires it
            whenever OSM data is drawn, whatever the cache says. */}
        {[ESRI_ATTRIBUTION, ...(context ? [OSM_ATTRIBUTION] : []), ...(context?.attribution ?? []).filter(a => !/openstreetmap/i.test(a))].join(' · ')}
      </div>
    </div>
  )
}
