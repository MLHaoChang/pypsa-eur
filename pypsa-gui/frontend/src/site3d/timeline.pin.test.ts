// Phase 2 plan Task 6.4 (pin): the 3D view reads the shared timeline
// (resultsSnapshotIdx) and never writes it — one time control for the app.
import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

describe('the 3D view and the timeline', () => {
  it('no site3d module or SiteCanvas writes resultsSnapshotIdx', () => {
    const dir = __dirname
    const files = [
      ...readdirSync(dir).filter(f => /\.tsx?$/.test(f) && !/\.test\./.test(f)).map(f => join(dir, f)),
      join(dir, '..', 'pages', 'SiteCanvas.tsx'),
    ]
    const writers = files.filter(f => /setResultsSnapshotIdx|resultsSnapshotIdx\s*:/.test(readFileSync(f, 'utf8')))
    expect(writers).toEqual([])
    expect(readFileSync(join(dir, '..', 'pages', 'SiteCanvas.tsx'), 'utf8')).toMatch(/useUIStore\(s => s\.resultsSnapshotIdx\)/)
  })
})
