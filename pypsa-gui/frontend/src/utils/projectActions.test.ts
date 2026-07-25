import { describe, expect, it } from 'vitest'
import { formatRelativeTime, nextUntitledName, slugify } from './projectActions'

describe('slugify', () => {
  it('lowercases and collapses runs of non-alphanumerics to a single _', () => {
    expect(slugify('My Project')).toBe('my_project')
    expect(slugify('Wind Farm #1')).toBe('wind_farm_1')
    expect(slugify('(draft) v2')).toBe('draft_v2')
  })

  it('trims underscores from BOTH ends', () => {
    // Regression: the trim was /^_|_$/ with no `g` flag, so the alternation
    // replaced only the first match and a title with junk at both ends kept
    // its trailing underscore.
    expect(slugify('!My Project!')).toBe('my_project')
    expect(slugify('  spaced  ')).toBe('spaced')
    expect(slugify('___a___')).toBe('a')
  })

  it('falls back to "network" when nothing survives', () => {
    expect(slugify('!!!')).toBe('network')
    expect(slugify('')).toBe('network')
    expect(slugify('   ')).toBe('network')
  })

  it('is idempotent — re-slugifying a slug is a no-op', () => {
    for (const s of ['My Project', '!My Project!', 'Wind Farm #1', '  spaced  ']) {
      expect(slugify(slugify(s))).toBe(slugify(s))
    }
  })
})

describe('nextUntitledName', () => {
  it('uses the bare name when it is free', () => {
    expect(nextUntitledName([])).toBe('Untitled')
    expect(nextUntitledName(['Something Else'])).toBe('Untitled')
  })

  it('starts numbering at 2 and skips every taken name', () => {
    expect(nextUntitledName(['Untitled'])).toBe('Untitled 2')
    expect(nextUntitledName(['Untitled', 'Untitled 2'])).toBe('Untitled 3')
    expect(nextUntitledName(['Untitled', 'Untitled 2', 'Untitled 4'])).toBe('Untitled 3')
  })

  it('never returns a name already in the list', () => {
    const taken = ['Untitled', ...Array.from({ length: 50 }, (_, i) => `Untitled ${i + 2}`)]
    expect(taken).not.toContain(nextUntitledName(taken))
  })
})

describe('formatRelativeTime', () => {
  const now = Date.parse('2026-07-25T12:00:00Z')
  const ago = (ms: number) => new Date(now - ms).toISOString()

  it('returns null for missing or unparseable input', () => {
    expect(formatRelativeTime(null)).toBeNull()
    expect(formatRelativeTime(undefined)).toBeNull()
    expect(formatRelativeTime('')).toBeNull()
    expect(formatRelativeTime('not a date')).toBeNull()
  })

  it('crosses each unit boundary at the right place', () => {
    expect(formatRelativeTime(ago(2_000), now)).toBe('just now')
    expect(formatRelativeTime(ago(30_000), now)).toBe('30s ago')
    expect(formatRelativeTime(ago(5 * 60_000), now)).toBe('5m ago')
    expect(formatRelativeTime(ago(3 * 3_600_000), now)).toBe('3h ago')
    expect(formatRelativeTime(ago(3 * 86_400_000), now)).toBe('3d ago')
  })

  it('switches to an absolute ISO-style date beyond a week', () => {
    // Deliberately not toLocaleDateString: Chrome follows the OS regional
    // setting, which is the same locale quirk documented for
    // <input type="datetime-local"> in CLAUDE.md.
    const out = formatRelativeTime(ago(30 * 86_400_000), now)
    expect(out).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  })

  it('clamps future timestamps to "just now" instead of going negative', () => {
    const future = new Date(now + 60_000).toISOString()
    expect(formatRelativeTime(future, now)).toBe('just now')
  })
})
