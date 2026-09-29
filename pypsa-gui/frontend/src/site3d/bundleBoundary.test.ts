// The bundle boundary (plan Task 4.1, v2 review F8): `three` is ~170 kB
// gzipped and must stay in the lazily-loaded SiteCanvas chunk. Every site3d
// module the MAIN bundle imports (the map view, the stores, the creation
// form, App) is listed here and may not import `three`, directly or through
// `@react-three/*`. A module that needs three (raycast, placement math,
// context geometry) is imported by SiteCanvas only and is not on this list.
import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

// Modules only SiteCanvas imports (they may import three). Everything else
// in this directory is main-bundle by default, so a new file is guarded
// without anyone remembering to list it.
const SITECANVAS_ONLY = new Set<string>(['raycast.ts', 'placementMath.ts', 'context.ts'])

const MAIN_BUNDLE_MODULES = readdirSync(__dirname)
  .filter(f => /\.(ts|tsx)$/.test(f) && !/\.test\./.test(f) && !SITECANVAS_ONLY.has(f))

describe('site3d bundle boundary', () => {
  it('covers the modules that exist', () => {
    expect(MAIN_BUNDLE_MODULES.length).toBeGreaterThanOrEqual(11)
  })
  it.each(MAIN_BUNDLE_MODULES)('%s does not import three', file => {
    const src = readFileSync(join(__dirname, file), 'utf8')
    expect(src).not.toMatch(/from ['"]three(\/|['"])/)
    expect(src).not.toMatch(/from ['"]@react-three\//)
    expect(src).not.toMatch(/import\(['"]three(\/|['"])/)
  })
})
