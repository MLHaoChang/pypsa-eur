// 3D site view — the spike from
// docs/superpowers/assessments/2026-09-28-3d-site-view-feasibility.md §8a.
//
// One bus, everything attached to it, as parametric boxes on a ground plane
// textured with the Esri imagery the map view already draws. Clicking a box
// selects the component exactly as the other two canvases do
// (`setSelectedComponent({type, name})`), so the properties panel opens and
// an edit there re-generates the geometry through the normal query
// invalidation — the scene holds no state of its own beyond hover and camera.
//
// Deliberately NOT in the spike (they are Phase 1–2 of the assessment): a site
// entity or boundary, terrain, building footprints, drag-from-palette, moving
// objects, results animation, hero models, offline caching.
//
// This module is loaded lazily (App.tsx) and is the only importer of three.js.

import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Canvas, useThree, type ThreeEvent } from '@react-three/fiber'
import { OrbitControls, Html } from '@react-three/drei'
import * as THREE from 'three'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import { networkApi } from '../api/network'
import { busLatLng } from '../utils/geo'
import { tileRangeAround, mosaicExtent, zoomFor, tileCount, type LngLat, type LocalExtent } from '../site3d/geo'
import { buildGroundMosaic, ESRI_ATTRIBUTION } from '../site3d/imagery'
import { buildSiteLayout, KIND_COLOR, KIND_LABEL, type SiteObject, type SiteKind } from '../site3d/layout'
import { toScene, toBoxArgs, chooseSiteBus, fitCamera, type Bounds } from '../site3d/scene'

// ── One object = one group of boxes, one click target ────────────────────────

function SiteObjectMesh({ obj, selected, hovered, onHover, onSelect }: {
  obj: SiteObject
  selected: boolean
  hovered: boolean
  onHover: (name: string | null) => void
  onSelect: (obj: SiteObject) => void
}) {
  const [ox, oy] = obj.origin
  const tint = selected ? '#ffffff' : hovered ? '#fde68a' : undefined
  const top = Math.max(...obj.parts.map(p => p.pos[2] + p.size[2] / 2), 2)
  return (
    <group
      name={`${obj.type}:${obj.name}`}
      position={toScene(ox, oy, 0)}
      onClick={(e: ThreeEvent<MouseEvent>) => { e.stopPropagation(); onSelect(obj) }}
      onPointerOver={(e: ThreeEvent<PointerEvent>) => { e.stopPropagation(); onHover(obj.name) }}
      onPointerOut={() => onHover(null)}
    >
      {obj.parts.map((p, i) => (
        <mesh
          key={i}
          position={toScene(p.pos[0], p.pos[1], p.pos[2])}
          // Local (east, north, up) rotations → scene axes: about up = about Y,
          // about east = about X, about north = about −Z.
          rotation={[p.rotX ?? 0, p.rotZ ?? 0, -(p.rotN ?? 0)]}
          castShadow
          receiveShadow
        >
          <boxGeometry args={toBoxArgs(p.size)} />
          <meshStandardMaterial
            color={tint ?? p.color ?? obj.color}
            emissive={selected ? obj.color : '#000000'}
            emissiveIntensity={selected ? 0.6 : 0}
            roughness={0.7}
            metalness={0.1}
          />
        </mesh>
      ))}
      {(selected || hovered) && (
        <Html position={toScene(0, 0, top + 4)} center zIndexRange={[200, 100]} style={{ pointerEvents: 'none' }}>
          <div className="whitespace-nowrap rounded bg-bg/95 border border-border px-2 py-1 text-[11px] text-text shadow">
            <div className="font-semibold">{obj.name}</div>
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
  // A plane rotated −90° about X has its +Y (image top) along −Z, which is
  // north in this frame — so the mosaic needs no flip.
  const flat: [number, number, number] = [-Math.PI / 2, 0, 0]
  return (
    <group>
      <mesh position={toScene(cx, cy, -0.05)} rotation={flat} onClick={onMiss}>
        <planeGeometry args={[w, d]} />
        {/* Keyed so the textured material is a NEW material, not the grey
            one with `map` patched in: a material compiled without a map
            keeps its mapless program when `map` is assigned later (the
            program cache key is only re-read on `needsUpdate`), and under
            some drivers that renders black rather than white. */}
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

// ── Camera: fit the packed site once per mount (the Canvas is keyed on the bus) ──

function FitCamera({ bounds }: { bounds: Bounds }) {
  const camera = useThree(s => s.camera)
  const controls = useThree(s => s.controls) as { target: THREE.Vector3; update: () => void } | null
  const size = useThree(s => s.size)
  // Once, with the size at mount: refitting on every resize would yank the
  // camera away from wherever the user orbited it each time a panel opens.
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

// ── The view ──────────────────────────────────────────────────────────────────

export default function SiteCanvas() {
  const { currentProject, selectedComponent, setSelectedComponent } = useUIStore()

  const { data: buses = [], isLoading } = useQuery({ queryKey: nk(currentProject, 'buses'), queryFn: networkApi.getBuses })
  const { data: generators = [] }   = useQuery({ queryKey: nk(currentProject, 'generators'),    queryFn: networkApi.getGenerators })
  const { data: storageUnits = [] } = useQuery({ queryKey: nk(currentProject, 'storage_units'), queryFn: networkApi.getStorageUnits })
  const { data: stores = [] }       = useQuery({ queryKey: nk(currentProject, 'stores'),        queryFn: networkApi.getStores })
  const { data: loads = [] }        = useQuery({ queryKey: nk(currentProject, 'loads'),         queryFn: networkApi.getLoads })
  const { data: transformers = [] } = useQuery({ queryKey: nk(currentProject, 'transformers'),  queryFn: networkApi.getTransformers })
  const { data: lines = [] }        = useQuery({ queryKey: nk(currentProject, 'lines'),         queryFn: networkApi.getLines })
  const { data: links = [] }        = useQuery({ queryKey: nk(currentProject, 'links'),         queryFn: networkApi.getLinks })

  // Which bus is the site. The initial choice follows the current selection;
  // afterwards the picker overlay owns it. Session state only — the spike has
  // no site entity to persist (assessment §6).
  const [siteBusName, setSiteBusName] = useState<string | null>(null)
  const siteBus = useMemo(
    () => chooseSiteBus(buses, siteBusName ? null : selectedComponent, siteBusName),
    // The selection only seeds the FIRST choice; later selections (clicking a
    // box) must not swing the view to another bus.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [buses, siteBusName],
  )
  const bus = buses.find(b => b.name === siteBus) ?? null
  const placedBuses = buses.filter(b => busLatLng(b) !== null)

  const layout = useMemo(() => bus
    ? buildSiteLayout({ bus: { name: bus.name, v_nom: bus.v_nom }, generators, storageUnits, stores, loads, transformers, lines, links })
    : null,
  [bus, generators, storageUnits, stores, loads, transformers, lines, links])

  // Ground: tile range around the bus sized to the layout, stitched once per
  // (bus, size). The texture is disposed when replaced.
  const origin: LngLat | null = useMemo(() => {
    const ll = bus ? busLatLng(bus) : null
    return ll ? { lat: ll[0], lng: ll[1] } : null
  }, [bus])
  // Keyed on the quantised half-size, not the layout object: a parameter
  // edit rebuilds the layout every time, and refetching 36 tiles because a
  // genset gained an enclosure is the one thing this must not do.
  const halfSizeM = layout?.halfSizeM ?? 0
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
  if (!bus || !layout || !ground || !origin) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-center px-8">
        <div className="text-[13px] font-medium text-text">No placed bus to build a site from</div>
        <div className="text-[12px] text-muted max-w-md">
          The 3D site view is built around a bus with coordinates. Switch to the Satellite view and place a bus on the map, then come back.
        </div>
      </div>
    )
  }

  const kindsPresent = Array.from(new Set(layout.objects.map(o => o.kind))) as SiteKind[]
  const selectedName = selectedComponent?.name ?? null
  const selectedType = selectedComponent?.type ?? null

  return (
    <div className="relative h-full w-full bg-canvas">
      {/* key: a new bus gets a fresh camera; a parameter edit does not. */}
      <Canvas
        key={bus.name}
        shadows={false}
        dpr={[1, 2]}
        camera={{ fov: 45, near: 1, far: 50_000 }}
        onPointerMissed={() => setSelectedComponent(null)}
      >
        <color attach="background" args={['#cfd8e3']} />
        <hemisphereLight args={['#ffffff', '#8a9bb0', 0.6]} />
        <directionalLight
          position={toScene(-layout.halfSizeM, -layout.halfSizeM * 0.6, layout.halfSizeM * 1.4)}
          intensity={1.6}
          castShadow
          shadow-mapSize={[2048, 2048]}
          shadow-camera-left={-layout.halfSizeM}
          shadow-camera-right={layout.halfSizeM}
          shadow-camera-top={layout.halfSizeM}
          shadow-camera-bottom={-layout.halfSizeM}
          shadow-camera-near={1}
          shadow-camera-far={layout.halfSizeM * 5}
          // A 2048² map over a km-wide site is ~1 m per texel; without a
          // normal-offset bias the ground self-shadows everywhere and renders
          // black (seen under SwiftShader, and on integrated GPUs).
          shadow-normalBias={1.5}
          shadow-bias={-0.0002}
        />
        <Ground extent={ground.extent} texture={texture} onMiss={() => setSelectedComponent(null)} />
        {layout.objects.map(o => (
          <SiteObjectMesh
            key={`${o.type}:${o.name}`}
            obj={o}
            selected={o.type === selectedType && o.name === selectedName}
            hovered={o.name === hovered}
            onHover={setHovered}
            onSelect={obj => setSelectedComponent({ type: obj.type, name: obj.name })}
          />
        ))}
        <OrbitControls
          makeDefault
          maxPolarAngle={Math.PI / 2 - 0.05}
          minDistance={20}
          maxDistance={layout.halfSizeM * 8}
          enableDamping
        />
        <FitCamera bounds={layout.bounds} />
      </Canvas>

      {/* Site picker — top-left, BELOW the switcher's row so the two never
          collide when the canvas column is narrow (assistant dock + tab
          panel open leave it ~440 px). Under the switcher's z band. */}
      <div className="absolute left-3 top-12 z-[400] flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md border border-border bg-bg/95 px-2 py-1.5 text-[11px] shadow max-w-[calc(100%-24px)]">
        <span className="text-muted">Site</span>
        <select
          className="bg-transparent text-text outline-none"
          value={bus.name}
          onChange={e => setSiteBusName(e.target.value)}
          aria-label="Site bus"
        >
          {placedBuses.map(b => <option key={b.name} value={b.name}>{b.name}</option>)}
        </select>
        <span className="text-muted">·</span>
        <span className="text-muted">{layout.objects.length - 1} asset{layout.objects.length === 2 ? '' : 's'}</span>
        <span className="text-muted">·</span>
        <span className="text-muted" title="Sum of every asset's land take from its parameters">
          land ≈ {(layout.totalAreaM2 / 10_000).toFixed(1)} ha
        </span>
      </div>

      {/* Legend — top-left, under the picker. The bottom edge belongs to
          the snapshot picker bar (centre) and the attribution (right). */}
      <div className="absolute left-3 top-[5.5rem] z-[400] flex flex-wrap gap-x-3 gap-y-1 rounded-md border border-border bg-bg/95 px-2 py-1.5 text-[11px] shadow max-w-[60%]">
        {kindsPresent.map(k => (
          <span key={k} className="flex items-center gap-1 text-muted">
            <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: KIND_COLOR[k] }} />
            {KIND_LABEL[k]}
          </span>
        ))}
      </div>

      {/* Attribution — bottom-right, required for the Esri tiles. */}
      <div className="absolute right-3 bottom-12 z-[400] max-w-[45%] rounded bg-bg/80 px-1.5 py-0.5 text-right text-[10px] text-muted">
        {tileStatus ? `${tileStatus} · ` : ''}{ESRI_ATTRIBUTION}
      </div>
    </div>
  )
}
