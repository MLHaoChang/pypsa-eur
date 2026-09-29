// 3D site view.
//
// Phase 1 (docs/superpowers/specs/2026-09-29-3d-site-view-phase1-design.md):
// a SITE — a user-drawn boundary grouping one or more buses — opened as a
// portal from the map. Every component attached to the site's primary bus
// is drawn as parametric boxes on a ground plane textured with the Esri
// imagery the map view already uses; the boundary is a ribbon on the
// ground; the status line says whether the plan fits the plot (D9).
//
// Clicking a box selects the component exactly as the other two canvases do
// (`setSelectedComponent({type, name})`), so the properties panel opens and
// an edit there re-generates the geometry through the normal query
// invalidation — the scene holds no state of its own beyond hover and camera.
//
// Frames. Everything in the scene is metres east/north of the SITE ORIGIN
// (the boundary's stored centroid). The packed layout is computed about the
// primary bus and shifted by that bus's offset from the origin
// (`busOffsets`). WP3 replaces the single-bus layout with one yard per bus.
//
// This module is loaded lazily (App.tsx) and is the only importer of three.js
// (and of the site3d modules that import it).

import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Canvas, useThree, type ThreeEvent } from '@react-three/fiber'
import { OrbitControls, Html, Line } from '@react-three/drei'
import * as THREE from 'three'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import { networkApi } from '../api/network'
import { busLatLng } from '../utils/geo'
import { tileRangeAround, mosaicExtent, zoomFor, tileCount, type LngLat, type LocalExtent } from '../site3d/geo'
import { buildGroundMosaic, ESRI_ATTRIBUTION } from '../site3d/imagery'
import { buildSiteLayout, KIND_COLOR, KIND_LABEL, type SiteObject, type SiteKind } from '../site3d/layout'
import { toScene, toBoxArgs, fitCamera, chooseSite, unionBounds, halfSizeFor, type Bounds } from '../site3d/scene'
import { useSitesStore } from '../site3d/sitesStore'
import { readActiveSite, writeActiveSite } from '../site3d/activeSite'
import { boundaryToLocal } from '../site3d/boundary'
import { busOffsets, primaryBus, siteBounds } from '../site3d/siteModel'
import { fitReport, formatHa, type FitObject } from '../site3d/fit'
import SiteEmptyState from '../components/SiteEmptyState'
import type { Bus } from '../api/types'
import type { Site } from '../site3d/types'

// ── One object = one group of boxes, one click target ────────────────────────

function SiteObjectMesh({ obj, selected, hovered, outside, onHover, onSelect }: {
  obj: SiteObject
  selected: boolean
  hovered: boolean
  /** Sticks out of the site boundary (fit check, D9). */
  outside: boolean
  onHover: (name: string | null) => void
  onSelect: (obj: SiteObject) => void
}) {
  const [ox, oy] = obj.origin
  const tint = selected ? '#ffffff' : hovered ? '#fde68a' : undefined
  const emissive = selected ? obj.color : outside ? '#dc2626' : '#000000'
  const emissiveIntensity = selected ? 0.6 : outside ? 0.45 : 0
  const top = Math.max(...obj.parts.map(p => p.pos[2] + p.size[2] / 2), 2)
  return (
    <group
      name={`${obj.type}:${obj.name}`}
      position={toScene(ox, oy, 0)}
      // Heading is clockwise from north; a rotation about the up axis by −heading.
      rotation={[0, (-obj.heading * Math.PI) / 180, 0]}
      onClick={(e: ThreeEvent<MouseEvent>) => { e.stopPropagation(); onSelect(obj) }}
      onPointerOver={(e: ThreeEvent<PointerEvent>) => { e.stopPropagation(); onHover(obj.name) }}
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
          <boxGeometry args={toBoxArgs(p.size)} />
          <meshStandardMaterial
            color={tint ?? p.color ?? obj.color}
            emissive={emissive}
            emissiveIntensity={emissiveIntensity}
            roughness={0.7}
            metalness={0.1}
          />
        </mesh>
      ))}
      {(selected || hovered) && (
        <Html position={toScene(0, 0, top + 4)} center zIndexRange={[200, 100]} style={{ pointerEvents: 'none' }}>
          <div className="whitespace-nowrap rounded bg-bg/95 border border-border px-2 py-1 text-[11px] text-text shadow">
            <div className="font-semibold">{obj.name}{outside ? <span className="ml-2 text-[10px] text-accent">outside the boundary</span> : null}</div>
            <div className="text-muted">{obj.summary}</div>
          </div>
        </Html>
      )}
    </group>
  )
}

// ── Ground plane: the tile mosaic, or a flat grey slab while it loads ────────
//
// The imagery is UNLIT (meshBasicMaterial): a satellite photo already has the
// sun baked in, and shading it again darkens it and doubles the shadows. The
// objects' shadows still land on it through a second, transparent
// ShadowMaterial plane a hair above — three.js's standard shadow-catcher.

function Ground({ extent, texture, onMiss }: { extent: LocalExtent; texture: THREE.Texture | null; onMiss: () => void }) {
  const w = extent.east - extent.west
  const d = extent.north - extent.south
  const cx = (extent.east + extent.west) / 2
  const cy = (extent.north + extent.south) / 2
  const flat: [number, number, number] = [-Math.PI / 2, 0, 0]
  return (
    <group>
      <mesh position={toScene(cx, cy, -0.05)} rotation={flat} onClick={onMiss}>
        <planeGeometry args={[w, d]} />
        {/* Keyed so the textured material is a NEW material, not the grey
            one with `map` patched in: a material compiled without a map
            keeps its mapless program when `map` is assigned later, and
            under some drivers that renders black rather than white. */}
        {texture
          ? <meshBasicMaterial key="imagery" map={texture} toneMapped={false} />
          : <meshBasicMaterial key="flat" color="#6b7280" />}
      </mesh>
      <mesh position={toScene(cx, cy, 0)} rotation={flat} receiveShadow>
        <planeGeometry args={[w, d]} />
        <shadowMaterial transparent opacity={0.35} />
      </mesh>
    </group>
  )
}

// ── Boundary ribbon on the ground ────────────────────────────────────────────

function BoundaryRibbon({ site }: { site: Site }) {
  const points = useMemo(() => {
    const pts = boundaryToLocal(site.boundary, site.origin).map(p => toScene(p.x, p.y, 0.4))
    return [...pts, pts[0]]
  }, [site])
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

function Site3dDebugHook({ objects, site }: { objects: SiteObject[]; site: Site }) {
  const camera = useThree(s => s.camera)
  const scene = useThree(s => s.scene)
  const gl = useThree(s => s.gl)
  const size = useThree(s => s.size)
  useEffect(() => {
    const project = (key: string) => {
      const o = scene.getObjectByName(key)
      if (!o) return null
      const v = new THREE.Vector3()
      o.getWorldPosition(v).project(camera)
      const rect = gl.domElement.getBoundingClientRect()
      return { x: rect.left + ((v.x + 1) / 2) * rect.width, y: rect.top + ((1 - v.y) / 2) * rect.height }
    }
    const hook = {
      project,
      objects: objects.map(o => ({ key: `${o.type}:${o.name}`, origin: o.origin, heading: o.heading, parts: o.parts.length, summary: o.summary })),
      site: { id: site.id, name: site.name, placements: site.placements },
      snapshot: () => { gl.render(scene, camera); return gl.domElement.toDataURL('image/png') },
    }
    ;(window as unknown as { __site3d?: unknown }).__site3d = hook
    return () => { delete (window as unknown as { __site3d?: unknown }).__site3d }
  }, [camera, scene, gl, size, objects, site])
  return null
}

// ── The view ──────────────────────────────────────────────────────────────────

export default function SiteCanvas() {
  const { currentProject, selectedComponent, setSelectedComponent } = useUIStore()
  const activeSiteId = useUIStore(s => s.activeSiteId)
  const setActiveSiteId = useUIStore(s => s.setActiveSiteId)
  const sitesDoc = useSitesStore(s => s.docFor(currentProject))

  const { data: buses = [], isLoading } = useQuery({ queryKey: nk(currentProject, 'buses'), queryFn: networkApi.getBuses })
  const { data: generators = [] }   = useQuery({ queryKey: nk(currentProject, 'generators'),    queryFn: networkApi.getGenerators })
  const { data: storageUnits = [] } = useQuery({ queryKey: nk(currentProject, 'storage_units'), queryFn: networkApi.getStorageUnits })
  const { data: stores = [] }       = useQuery({ queryKey: nk(currentProject, 'stores'),        queryFn: networkApi.getStores })
  const { data: loads = [] }        = useQuery({ queryKey: nk(currentProject, 'loads'),         queryFn: networkApi.getLoads })
  const { data: transformers = [] } = useQuery({ queryKey: nk(currentProject, 'transformers'),  queryFn: networkApi.getTransformers })
  const { data: lines = [] }        = useQuery({ queryKey: nk(currentProject, 'lines'),         queryFn: networkApi.getLines })
  const { data: links = [] }        = useQuery({ queryKey: nk(currentProject, 'links'),         queryFn: networkApi.getLinks })

  // The remembered site for this project seeds the choice once per project.
  useEffect(() => {
    if (!activeSiteId) {
      const stored = readActiveSite(currentProject)
      if (stored) setActiveSiteId(stored)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentProject])

  const selectedBus = selectedComponent?.type === 'Bus' ? selectedComponent.name : null
  const site = useMemo(() => chooseSite(sitesDoc, activeSiteId, selectedBus), [sitesDoc, activeSiteId, selectedBus])

  const layoutForBus = (busName: string) => {
    const bus = buses.find(b => b.name === busName)
    return buildSiteLayout({ bus: { name: busName, v_nom: bus?.v_nom ?? 0 }, generators, storageUnits, stores, loads, transformers, lines, links })
  }

  const primary = site ? primaryBus(site) : null
  const offsets = useMemo(() => (site ? busOffsets(site, buses as Bus[]) : {}), [site, buses])
  const busOff: [number, number] = (primary && offsets[primary]) || [0, 0]

  const layout = useMemo(() => (primary ? layoutForBus(primary) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [primary, buses, generators, storageUnits, stores, loads, transformers, lines, links])

  // Objects in the SITE frame: the packed layout shifted by the primary bus's offset.
  const objects: SiteObject[] = useMemo(() => layout
    ? layout.objects.map(o => ({ ...o, origin: [o.origin[0] + busOff[0], o.origin[1] + busOff[1]] as [number, number] }))
    : [], [layout, busOff[0], busOff[1]]) // eslint-disable-line react-hooks/exhaustive-deps

  const siteExtent: Bounds | null = useMemo(() => {
    if (!site || !layout) return null
    const shifted = { x0: layout.bounds.x0 + busOff[0], x1: layout.bounds.x1 + busOff[0], y0: layout.bounds.y0 + busOff[1], y1: layout.bounds.y1 + busOff[1] }
    return unionBounds(siteBounds(site), shifted)
  }, [site, layout, busOff[0], busOff[1]]) // eslint-disable-line react-hooks/exhaustive-deps
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
  useEffect(() => {
    if (!ground) return
    let cancelled = false
    setTexture(null)
    setTileStatus(`fetching ${tileCount(ground.range)} tiles at z${ground.z}…`)
    buildGroundMosaic(ground.range).then(({ canvas, missing }) => {
      if (cancelled) return
      const t = new THREE.CanvasTexture(canvas)
      t.colorSpace = THREE.SRGBColorSpace
      t.anisotropy = 8
      setTexture(t)
      setTileStatus(`${tileCount(ground.range)} tiles at z${ground.z}${missing ? ` · ${missing} missing` : ''}`)
    }).catch((err: unknown) => {
      if (cancelled) return
      setTileStatus(`imagery unavailable: ${err instanceof Error ? err.message : String(err)}`)
    })
    return () => { cancelled = true }
  }, [ground])
  useEffect(() => () => { texture?.dispose() }, [texture])

  const [hovered, setHovered] = useState<string | null>(null)

  // ── Empty states ──────────────────────────────────────────────────────────
  if (isLoading) {
    return <div className="flex h-full items-center justify-center text-[12px] text-muted">Loading network…</div>
  }
  if (!site) {
    return <SiteEmptyState buses={buses as Bus[]} boundsFor={name => layoutForBus(name).bounds} />
  }
  if (!layout || !ground || !siteExtent || !fit) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-center px-8">
        <div className="text-[13px] font-medium text-text">{site.name} has no placed bus</div>
        <div className="text-[12px] text-muted max-w-md">Add a placed bus to this site (Satellite view → click the boundary → Edit buses).</div>
      </div>
    )
  }

  const kindsPresent = Array.from(new Set(objects.map(o => o.kind))) as SiteKind[]
  const selectedName = selectedComponent?.name ?? null
  const selectedType = selectedComponent?.type ?? null
  const primaryPlaced = primary != null && offsets[primary] != null

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
        <Ground extent={ground.extent} texture={texture} onMiss={() => setSelectedComponent(null)} />
        <BoundaryRibbon site={site} />
        {objects.map(o => (
          <SiteObjectMesh
            key={`${o.type}:${o.name}`}
            obj={o}
            selected={o.type === selectedType && o.name === selectedName}
            hovered={o.name === hovered}
            outside={outsideSet.has(`${o.type}:${o.name}`)}
            onHover={setHovered}
            onSelect={obj => setSelectedComponent({ type: obj.type, name: obj.name })}
          />
        ))}
        <OrbitControls
          makeDefault
          maxPolarAngle={Math.PI / 2 - 0.05}
          minDistance={20}
          maxDistance={halfSizeM * 8}
          enableDamping
        />
        <FitCamera bounds={siteExtent} />
        {DEBUG_ENABLED && <Site3dDebugHook objects={objects} site={site} />}
      </Canvas>

      {/* Site picker + fit check — top-left, BELOW the switcher's row. */}
      <div className="absolute left-3 top-12 z-[400] flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md border border-border bg-bg/95 px-2 py-1.5 text-[11px] shadow max-w-[calc(100%-24px)]">
        <span className="text-muted">Site</span>
        <select
          className="bg-transparent text-text outline-none"
          value={site.id}
          onChange={e => { setActiveSiteId(e.target.value); writeActiveSite(currentProject, e.target.value) }}
          aria-label="Site"
        >
          {sitesDoc.sites.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        <span className="text-muted">·</span>
        <span className="text-muted">{objects.length - 1} asset{objects.length === 2 ? '' : 's'}</span>
        <span className="text-muted">·</span>
        <span
          data-testid="fit-status"
          className={fit.over ? 'text-accent font-semibold' : 'text-muted'}
          title="Sum of every asset's land take from its parameters, against the plot area inside the boundary"
        >
          land {formatHa(fit.landM2)} · plot {formatHa(fit.plotM2)}{fit.over ? ' · does not fit' : ''}
        </span>
        {fit.outside.length > 0 && (
          <span className="text-accent" title={fit.outside.join(', ')}>· {fit.outside.length} outside</span>
        )}
        {!primaryPlaced && <span className="text-accent">· primary bus not placed</span>}
      </div>

      {/* Legend — under the picker. */}
      <div className="absolute left-3 top-[5.5rem] z-[400] flex flex-wrap gap-x-3 gap-y-1 rounded-md border border-border bg-bg/95 px-2 py-1.5 text-[11px] shadow max-w-[60%]">
        {kindsPresent.map(k => (
          <span key={k} className="flex items-center gap-1 text-muted">
            <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: KIND_COLOR[k] }} />
            {KIND_LABEL[k]}
          </span>
        ))}
      </div>

      {/* Attribution — bottom-right above the snapshot bar, required for the Esri tiles. */}
      <div className="absolute right-3 bottom-12 z-[400] max-w-[45%] rounded bg-bg/80 px-1.5 py-0.5 text-right text-[10px] text-muted">
        {tileStatus ? `${tileStatus} · ` : ''}{ESRI_ATTRIBUTION}
      </div>
    </div>
  )
}
