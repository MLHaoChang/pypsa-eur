// Phase 2 plan Task 3.2: the offline desktop app must never fetch a decoder
// or a model from a CDN. drei's useGLTF defaults to the Draco decoder on
// gstatic.com unless its second argument is false.
import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

const CDN = /gstatic\.com|draco\/versioned|jsdelivr|unpkg\.com|\.wasm['"]/
const USE_GLTF = /useGLTF(?:\.preload)?\(/g
// A decoder is only ever needed for compressed models, and its default path is a CDN.
const DECODER = /\b(?:DRACOLoader|MeshoptDecoder|KTX2Loader|setDecoderPath)\b/

/** The top-level arguments of a call whose '(' is at `open`, bracket-aware. */
function callArgs(code: string, open: number): string[] {
  const args: string[] = []
  let depth = 0, start = open + 1
  for (let i = open; i < code.length; i++) {
    const c = code[i]
    if (c === '(' || c === '[' || c === '{') depth++
    else if (c === ')' || c === ']' || c === '}') {
      depth--
      if (depth === 0) { args.push(code.slice(start, i).trim()); return args }
    } else if (c === ',' && depth === 1) { args.push(code.slice(start, i).trim()); start = i + 1 }
  }
  return args
}

/** Violations in one source text: a CDN reference, a decoder, or a useGLTF / useGLTF.preload call whose 2nd argument is not `false`. */
export function cdnViolations(src: string): string[] {
  const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1')
  const out: string[] = []
  if (CDN.test(code)) out.push('cdn reference')
  if (DECODER.test(code)) out.push('decoder reference')
  for (const m of code.matchAll(USE_GLTF)) {
    const args = callArgs(code, m.index! + m[0].length - 1)
    if (args[1] !== 'false') out.push(`useGLTF without useDraco=false: ${m[0]}${args.join(', ')})`)
  }
  return out
}

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap(e => {
    const p = join(dir, e.name)
    return e.isDirectory() ? walk(p) : /\.(ts|tsx)$/.test(e.name) && !/\.test\./.test(e.name) ? [p] : []
  })
}

describe('no CDN', () => {
  it('the check itself catches each case', () => {
    expect(cdnViolations("const u = 'https://www.gstatic.com/draco/versioned/decoders/1.5.5/'")).not.toEqual([])
    expect(cdnViolations('useGLTF(url)')).not.toEqual([])
    expect(cdnViolations('useGLTF(url, true)')).not.toEqual([])
    expect(cdnViolations('useGLTF.preload(url)')).not.toEqual([])
    expect(cdnViolations('useGLTF(url, false, false)')).toEqual([])
    expect(cdnViolations('useGLTF.preload(url, false, false)')).toEqual([])
    expect(cdnViolations('// see gstatic.com — the pitfall\nuseGLTF(u, false, false)')).toEqual([])
    expect(cdnViolations('useGLTF(url(m), false, false)')).toEqual([])
    expect(cdnViolations('useGLTF(url(m))')).not.toEqual([])
    expect(cdnViolations('useGLTF.preload(url(m, a), true, false)')).not.toEqual([])
    expect(cdnViolations("import { DRACOLoader } from 'three/examples/jsm/loaders/DRACOLoader.js'")).not.toEqual([])
    expect(cdnViolations('loader.setMeshoptDecoder(MeshoptDecoder)')).not.toEqual([])
    expect(cdnViolations('new GLTFLoader().loadAsync(url)')).toEqual([])
  })
  it('no source file under src/ references a CDN or loads a model with the default decoder', () => {
    const bad = walk(join(__dirname, '..')).flatMap(f => cdnViolations(readFileSync(f, 'utf8')).map(v => `${f}: ${v}`))
    expect(bad).toEqual([])
  })
})
