// Read a GLB's JSON chunk (Phase 2 plan Task 3.1): the 12-byte header, then
// the first chunk (length, type 'JSON', payload). Pure; used by tests to pin
// a model's node tree and bounds without a GLTF loader (which cannot parse
// in jsdom).

export interface GltfJson {
  scene?: number
  scenes: { nodes: number[] }[]
  nodes: { name?: string; mesh?: number; children?: number[]; translation?: number[]; rotation?: number[]; scale?: number[]; matrix?: number[] }[]
  meshes: { primitives: { attributes: Record<string, number> }[] }[]
  accessors: { min?: number[]; max?: number[] }[]
  extensionsUsed?: string[]
  extensionsRequired?: string[]
}

export function readGlbJson(bytes: Uint8Array): GltfJson {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  if (view.getUint32(0, true) !== 0x46546c67) throw new Error('not a GLB (bad magic)')
  if (view.getUint32(4, true) !== 2) throw new Error('not glTF 2.0')
  const len = view.getUint32(12, true)
  if (view.getUint32(16, true) !== 0x4e4f534a) throw new Error('first chunk is not JSON')
  return JSON.parse(new TextDecoder().decode(bytes.subarray(20, 20 + len))) as GltfJson
}
