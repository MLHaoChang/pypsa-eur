// SC-04 independent oracle (GK B plan review): three's own GLTFLoader reads the
// GLB the backend writer produced. The fixture is generated, never hand-edited:
// `python -m tests.site_scene_export_fixture` from pypsa-gui/backend, and the
// backend test byte-compares it with that generator.
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { Box3, Mesh, Vector3, type Object3D } from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'

const bytes = readFileSync(join(__dirname, '__fixtures__', 'scene-export.glb'))

function load(): Promise<Object3D> {
  // A fresh ArrayBuffer of this realm: GLTFLoader tells binary from text with `instanceof ArrayBuffer`.
  const buf = new ArrayBuffer(bytes.length)
  new Uint8Array(buf).set(bytes)
  return new Promise((resolve, reject) => new GLTFLoader().parse(buf, '', g => resolve(g.scene), reject))
}

const size = (o: Object3D) => new Box3().setFromObject(o).getSize(new Vector3())
// three rewrites node names (it drops the ':'), so the ids are read where the writer puts them: glTF extras.
const byId = (scene: Object3D, id: string) => {
  let hit: Object3D | undefined
  scene.traverse(o => { if (o.userData.feature_id === id) hit = o })
  if (!hit) throw new Error(`no node with feature_id ${id}`)
  return hit
}

describe('the exported GLB through three.js GLTFLoader', () => {
  it('has the site root and one named node per feature, with the ids as userData (glTF extras)', async () => {
    const scene = await load()
    let root: Object3D | undefined
    scene.traverse(o => { if (o.userData.kind === 'site') root = o })
    expect(root!.userData.site_id).toBe('campus')
    expect(root!.children.map(c => c.userData.feature_id).sort()).toEqual(['Generator:pv_roof', 'greenfield:hall', 'osm:42'])
    const hall = byId(scene, 'greenfield:hall')
    expect(hall.userData).toMatchObject({ feature_id: 'greenfield:hall', kind: 'building', source_class: 'proposed' })
    expect(hall.userData.bindings).toEqual([{ class: 'Load', uid: 'u-office', name_at_bind: 'office', role: 'consumer' }])
    expect(byId(scene, 'osm:42').userData.source_class).toBe('mapped')
  })

  it('keeps metres, Y-up and north at -Z: the L building spans 20 m east, 20 m north and 12 m up', async () => {
    const scene = await load()
    scene.updateMatrixWorld(true)
    const l = byId(scene, 'osm:42')
    const box = new Box3().setFromObject(l)
    expect(box.min.x).toBeCloseTo(40, 4)
    expect(box.max.x).toBeCloseTo(60, 4)
    expect(box.min.y).toBeCloseTo(0, 4)
    expect(box.max.y).toBeCloseTo(12, 4)
    expect(box.min.z).toBeCloseTo(-20, 4)          // 20 m north
    expect(box.max.z).toBeCloseTo(0, 4)
  })

  it('places equipment as a native-size box rotated by its heading, with no node scale', async () => {
    const scene = await load()
    scene.updateMatrixWorld(true)
    const pv = byId(scene, 'Generator:pv_roof')
    expect(pv).toBeInstanceOf(Mesh)
    expect(pv.scale.toArray()).toEqual([1, 1, 1])
    expect(pv.position.toArray()).toEqual([30, 0, -10])
    // Heading 30 (clockwise from north) is a rotation of -30 degrees about +Y.
    expect(pv.rotation.y).toBeCloseTo(-Math.PI / 6, 6)
    const local = (pv as Mesh).geometry
    local.computeBoundingBox()
    const s = local.boundingBox!.getSize(new Vector3())
    expect([s.x, s.y, s.z].map(v => +v.toFixed(5))).toEqual([4, 2.5, 2])
    expect(size(pv).y).toBeCloseTo(2.5, 5)
  })
})
