// The frontend half of the `tool_error` kind manifest
// (`pypsa-gui/tool-error-kinds.json`).
//
// THE HOLE THIS FILLS. `ChatPanel.profile.test.tsx` proves
// TOOL_ERROR_BANNER_KINDS ⊆ KIND_COPY — it catches a routed kind losing its
// copy. It cannot catch the other direction: a kind the backend emits that
// SHOULD route and does not. That is the direction that actually bit us, and
// its symptom is silent — the copy exists and is simply unreachable, so the
// user gets a truncated gray tool line instead of the banner. That is how
// `inactive_acting_user` shipped wrong, and `not_authorized` and
// `unknown_profile_id` after it.
//
// The missing half was never a frontend fact: this file cannot enumerate what
// the backend emits. So the backend derives it into the manifest (see
// `backend/tests/test_tool_error_kind_manifest.py`, which fails when a new
// error_kind appears) and this asserts the UI still honours it.
//
// Read with `readFileSync` rather than imported: the manifest sits above the
// vite root, so an `import` would not resolve. Resolved from `process.cwd()`
// (the frontend package root under vitest) rather than `import.meta.url`,
// which is not a file: URL once vite has transformed this module.
//
// A path that resolved to nothing would leave every case below iterating an
// empty array and reporting green — the exact failure this pair exists to
// stop — so the load is asserted first.
//
// The reference below is FILE-SCOPED on purpose. `tsconfig.json` sets
// `"types": []` so application code cannot reach for node globals by
// accident, and that rule is worth keeping — this is a test that genuinely
// reads a file off disk, so it opts itself in rather than widening the
// setting for every module. (`@types/node` is already present via the
// toolchain; if it ever stops being, tsc fails here with a clear message
// rather than anything silent.)
/// <reference types="node" />
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { KIND_COPY, TOOL_ERROR_BANNER_KINDS } from './ChatPanel'

const MANIFEST_PATH = resolve(process.cwd(), '..', 'tool-error-kinds.json')

type Entry = { surface: 'banner' | 'inline'; why: string }
const manifest = JSON.parse(readFileSync(MANIFEST_PATH, 'utf-8')) as {
  kinds: Record<string, Entry>
}
const entries = Object.entries(manifest.kinds)
const banner = entries.filter(([, e]) => e.surface === 'banner').map(([k]) => k)
const inline = entries.filter(([, e]) => e.surface === 'inline').map(([k]) => k)

describe('tool_error kind manifest', () => {
  it('was actually loaded — an empty manifest would make every case below vacuous', () => {
    expect(entries.length).toBeGreaterThan(40)
    expect(banner.length).toBeGreaterThan(0)
    expect(inline.length).toBeGreaterThan(0)
  })

  // THE DIRECTION NOTHING GUARDED. A kind the backend can put on a tool_error
  // frame, that the manifest says deserves the banner, must actually reach it.
  it.each(banner)('%s is routed to the banner', (kind) => {
    expect(
      TOOL_ERROR_BANNER_KINDS.has(kind),
      `${kind} is 'banner' in the manifest but missing from ` +
        'TOOL_ERROR_BANNER_KINDS, so its copy is unreachable on a tool_error ' +
        'frame and the user sees a truncated gray tool line',
    ).toBe(true)
  })

  it.each(banner)('%s has copy to render', (kind) => {
    expect(
      Object.prototype.hasOwnProperty.call(KIND_COPY, kind),
      `${kind} routes to the banner with no KIND_COPY entry, so the banner ` +
        'prints the raw snake_case kind as its title',
    ).toBe(true)
  })

  // The reverse, so the manifest cannot quietly disagree with the UI in the
  // other direction either: a kind deliberately left inline must not be
  // routed. Without this, "inline" in the manifest would be a comment.
  it.each(inline)('%s stays an inline tool line', (kind) => {
    expect(
      TOOL_ERROR_BANNER_KINDS.has(kind),
      `${kind} is 'inline' in the manifest but IS routed to the banner. ` +
        'One of the two is wrong — if the routing is right, change the ' +
        'manifest and say why there.',
    ).toBe(false)
  })
})
