// The bundle boundary (Phase 1 plan Task 4.1, v2 review F8; Phase 2 plan
// Task 0.2): `three` is ~170 kB gzipped and must stay in the lazily-loaded
// SiteCanvas chunk. Every site3d module the MAIN bundle imports (the map
// view, the stores, the creation form, App) is checked here and may not
// import `three`, directly or through `@react-three/*`. A module that needs
// three is SiteCanvas-only: listed below, and imported only by
// `pages/SiteCanvas.tsx` or by another SiteCanvas-only module (Phase 2's
// modules are `.tsx` and import each other).
import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

// Modules only SiteCanvas imports (they may import three). Everything else
// in this directory is main-bundle by default, so a new file is guarded
// without anyone remembering to list it.
const SITECANVAS_ONLY = new Set<string>(['raycast.ts', 'placementMath.ts', 'context.ts', 'partGeometry.ts', 'objectGeometry.ts', 'heroLoader.tsx', 'resultsLayer.tsx'])

const MAIN_BUNDLE_MODULES = readdirSync(__dirname)
  .filter(f => /\.(ts|tsx)$/.test(f) && !/\.test\./.test(f) && !SITECANVAS_ONLY.has(f))

const THREE_IMPORT = [/from ['"]three(\/|['"])/, /from ['"]@react-three\//, /import\(['"]three(\/|['"])/]

/**
 * Files under `root` (relative paths) that import the SiteCanvas-only
 * module `file` (which lives in `root/site3d/`) but are not allowed to:
 * anything other than `siteCanvas` and the other SiteCanvas-only modules.
 */
export function forbiddenImporters(root: string, file: string, siteCanvasOnly: Set<string>, siteCanvas = 'pages/SiteCanvas.tsx'): string[] {
  const stem = file.replace(/\.tsx?$/, '')
  const allowed = new Set([siteCanvas, ...[...siteCanvasOnly].map(f => `site3d/${f}`)])
  return walk(root)
    .filter(p => !/\.test\.tsx?$/.test(p))
    .filter(p => {
      const src = readFileSync(p, 'utf8')
      return new RegExp(`from ['"][./]*site3d/${stem}['"]`).test(src) || new RegExp(`from ['"]\\./${stem}['"]`).test(src)
    })
    .map(p => p.replace(root + '/', ''))
    .filter(rel => !allowed.has(rel))
}

describe('site3d bundle boundary', () => {
  it('covers the modules that exist', () => {
    expect(MAIN_BUNDLE_MODULES.length).toBeGreaterThanOrEqual(11)
  })
  it.each(MAIN_BUNDLE_MODULES)('%s does not import three', file => {
    const src = readFileSync(join(__dirname, file), 'utf8')
    for (const re of THREE_IMPORT) expect(src).not.toMatch(re)
  })
})

describe('SiteCanvas-only modules', () => {
  it.each([...SITECANVAS_ONLY].filter(f => existsSync(join(__dirname, f))))('%s is imported only by SiteCanvas or another SiteCanvas-only module', file => {
    expect(forbiddenImporters(join(__dirname, '..'), file, SITECANVAS_ONLY)).toEqual([])
  })
})

/**
 * The relative-import closure of `entry` (a path relative to `root`):
 * the entry and every module it reaches through `from './x'` /
 * `from '../y'` / `import('./z')`, as paths relative to `root`. Package
 * imports are not followed; they are what THREE_IMPORT inspects per file.
 */
export function importClosure(root: string, entry: string): string[] {
  const seen = new Set<string>()
  const todo = [entry]
  while (todo.length) {
    const rel = todo.pop()!
    if (seen.has(rel)) continue
    seen.add(rel)
    const src = readFileSync(join(root, rel), 'utf8')
    for (const m of src.matchAll(/(?:from|import)\s*\(?\s*['"](\.{1,2}\/[^'"]+)['"]/g)) {
      const target = resolveModule(root, join(rel, '..', m[1]))
      if (target) todo.push(target)
    }
  }
  return [...seen].sort()
}

function resolveModule(root: string, rel: string): string | null {
  for (const candidate of [rel, `${rel}.ts`, `${rel}.tsx`, join(rel, 'index.ts'), join(rel, 'index.tsx')]) {
    if (/\.(ts|tsx)$/.test(candidate) && existsSync(join(root, candidate))) return candidate
  }
  return null
}

// The shared asset taxonomy (visual-layers plan 3 S1) is what the schematic
// and the map import to draw a type's colour, label and icon in the MAIN
// bundle. It and everything it reaches must stay free of three and of
// site3d/ (a site3d module may import it, never the other way round).
describe('utils/assetTypes is main-bundle-safe', () => {
  const root = join(__dirname, '..')
  const SHARED = ['utils/assetTypes.ts', 'utils/assetTypeIcon.tsx']
  it.each(SHARED)('%s exists', entry => {
    expect(existsSync(join(root, entry))).toBe(true)
  })
  it.each(SHARED)('%s and everything it imports stay out of three and site3d/', entry => {
    const closure = importClosure(root, entry)
    expect(closure).toContain(entry)
    expect(closure.filter(p => p.startsWith('site3d/'))).toEqual([])
    for (const p of closure) {
      const src = readFileSync(join(root, p), 'utf8')
      for (const re of THREE_IMPORT) expect(src, p).not.toMatch(re)
    }
  })
  it('the taxonomy itself reaches only utils/ (no React, no layout, no components)', () => {
    expect(importClosure(root, 'utils/assetTypes.ts').every(p => p.startsWith('utils/'))).toBe(true)
    expect(readFileSync(join(root, 'utils/assetTypes.ts'), 'utf8')).not.toMatch(/from ['"]react['"]/)
  })
  it('the closure walk itself follows a chain and resolves .tsx (fixtures)', () => {
    const fixtures = join(__dirname, '__fixtures__', 'boundary')
    expect(importClosure(fixtures, 'site3d/fixtureChain.ts')).toEqual(['site3d/fixtureChain.ts', 'site3d/fixtureMesh.tsx'])
  })
})

describe('the importer check itself (fixtures)', () => {
  const root = join(__dirname, '__fixtures__', 'boundary')
  const only = new Set(['fixtureMesh.tsx', 'fixtureChain.ts'])
  it('accepts a .tsx module imported by another SiteCanvas-only module', () => {
    expect(forbiddenImporters(root, 'fixtureMesh.tsx', only)).toEqual([])
  })
  it('sees a .tsx module\'s importers at all', () => {
    // The chain module imports fixtureMesh.tsx; when the chain is NOT
    // SiteCanvas-only it must be reported. (A `.ts`-only stem strip never
    // found `.tsx` importers, so this returned [] and passed vacuously.)
    expect(forbiddenImporters(root, 'fixtureMesh.tsx', new Set(['fixtureMesh.tsx']))).toEqual(['site3d/fixtureChain.ts'])
  })
  it('rejects a main-bundle module importing a SiteCanvas-only one', () => {
    expect(forbiddenImporters(root, 'fixtureChain.ts', only)).toEqual(['site3d/fixtureMain.ts'])
  })
})

function walk(dir: string): string[] {
  const out: string[] = []
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name)
    if (e.isDirectory()) out.push(...walk(p))
    else if (/\.(ts|tsx)$/.test(e.name)) out.push(p)
  }
  return out
}
