// SC-01b: the native scene's buildings in Site 3D. Accepted buildings are
// solid; a proposal's preview is drawn as translucent ghosts over them, so the
// user sees what Apply would commit. A click selects a building by its stable
// id (`sitePartSelection`). Imports three: SiteCanvas-only (bundle guard).
import { useEffect, useMemo } from 'react'
import type { ThreeEvent } from '@react-three/fiber'
import { BufferGeometry, Float32BufferAttribute } from 'three'
import type { SceneBuilding } from '../api/siteScene'
import { footprintToParts, type HeightAt } from './context'

const ACCEPTED = '#cfd3da'
const SELECTED = '#b3261e'

function geometryFor(b: SceneBuilding, heightAt: HeightAt): BufferGeometry {
  const ring = b.footprint.map(([x, y]) => ({ x, y }))
  const cx = ring.reduce((s, p) => s + p.x, 0) / ring.length
  const cy = ring.reduce((s, p) => s + p.y, 0) / ring.length
  const parts = footprintToParts(ring, Math.max(b.height_m, 0.5), heightAt(cx, cy))
  const g = new BufferGeometry()
  g.setAttribute('position', new Float32BufferAttribute(parts.positions, 3))
  g.setIndex(parts.indices)
  const flat = g.toNonIndexed()
  g.dispose()
  flat.computeVertexNormals()
  return flat
}

function Building({ b, heightAt, ghost, selected, onSelect }: {
  b: SceneBuilding; heightAt: HeightAt; ghost: boolean; selected: boolean; onSelect?: (id: string) => void
}) {
  const geometry = useMemo(() => geometryFor(b, heightAt), [b, heightAt])
  useEffect(() => () => geometry.dispose(), [geometry])
  const click = onSelect ? (e: ThreeEvent<MouseEvent>) => { e.stopPropagation(); onSelect(b.id) } : undefined
  return (
    <mesh geometry={geometry} castShadow={!ghost} receiveShadow={!ghost} onClick={click} userData={{ buildingId: b.id }}>
      <meshStandardMaterial color={selected ? SELECTED : ghost ? SELECTED : ACCEPTED} transparent={ghost} opacity={ghost ? 0.35 : 1}
        depthWrite={!ghost} roughness={0.9} metalness={0} />
    </mesh>
  )
}

export default function NativeBuildings({ accepted, preview, selectedId, heightAt, onSelect }: {
  accepted: SceneBuilding[]
  /** The proposal's full building list, or null when no proposal is under review. */
  preview: SceneBuilding[] | null
  selectedId: string | null
  heightAt: HeightAt
  onSelect: (id: string) => void
}) {
  // Under review, the preview replaces the accepted set it was built from.
  const shown = preview ?? accepted
  return (
    <group name="native-buildings">
      {shown.map(b => (
        <Building key={`${preview ? 'p' : 'a'}:${b.id}`} b={b} heightAt={heightAt} ghost={!!preview}
          selected={b.id === selectedId} onSelect={preview ? undefined : onSelect} />
      ))}
    </group>
  )
}
