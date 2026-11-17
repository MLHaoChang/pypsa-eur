// THEME-LIGHT-1: a solid status fill (bg-danger, bg-warn, bg-success,
// bg-info, at 60 % opacity or more) must name a foreground that follows the
// theme. `text-white` reads on the light theme's deep status colours but
// drops to ~2.2 : 1 on the dark theme's bright ones. theme.contrast.test.ts
// pins the two allowed foregrounds (on-status, bg) on every fill in both themes.
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { describe, expect, it } from 'vitest'

const SRC = __dirname
const FILL = /(?:^|[\s'"`])bg-(danger|warn|success|info)(?:\/([6-9]\d|100))?(?=[\s'"`]|$)/
const FOREGROUND = /(?:^|[\s'"`])text-(on-status|bg)(?=[\s'"`]|$)/

// Fills that carry no text, keyed per literal (file + exact literal) so a new
// text-bearing fill in the same file is still checked.
const NO_TEXT = new Set([
  'components/FlowStateDot.tsx:"block h-2 w-2 rounded-full bg-warn"', // status dot
  "components/PageKit.tsx:on ? 'bg-success' : 'bg-border-2'}`", // toggle track; its knob is a white disc, not text
])

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap(name => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return sources(path)
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [path] : []
  })
}

/** Quoted string literals; template literals are split at `${` so each branch stands alone. */
function literals(src: string): string[] {
  return [...src.matchAll(/'[^'\n]*'|"[^"\n]*"|`[^`]*`/g)].flatMap(m => m[0].split('${'))
}

describe('solid status fills name a theme-aware foreground', () => {
  it('no status fill relies on text-white or an inherited colour', () => {
    // Every allowlisted literal still exists, so a stale entry can't hide a new one.
    for (const key of NO_TEXT) {
      const [rel, lit] = [key.slice(0, key.indexOf(':')), key.slice(key.indexOf(':') + 1)]
      expect(readFileSync(join(SRC, rel), 'utf8'), `stale allowlist entry ${key}`).toContain(lit)
    }
    const offenders: string[] = []
    for (const file of sources(SRC)) {
      const rel = relative(SRC, file)
      for (const lit of literals(readFileSync(file, 'utf8'))) {
        if (NO_TEXT.has(`${rel}:${lit.trim()}`)) continue
        if (FILL.test(lit) && !FOREGROUND.test(lit)) offenders.push(`${rel}: ${lit.trim().slice(0, 90)}`)
      }
    }
    expect(offenders).toEqual([])
  })

  it('text-bearing status fills stay at 90 % opacity or more (hover included)', () => {
    const faint: string[] = []
    for (const file of sources(SRC)) {
      const rel = relative(SRC, file)
      for (const lit of literals(readFileSync(file, 'utf8'))) {
        if (FOREGROUND.test(lit) && /bg-(danger|warn|success|info)\/[6-8]\d\b/.test(lit)) faint.push(`${rel}: ${lit.trim().slice(0, 90)}`)
      }
    }
    expect(faint).toEqual([])
  })
})
