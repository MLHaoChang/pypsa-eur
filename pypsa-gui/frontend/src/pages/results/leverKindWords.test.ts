import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { LEVER_KIND_WORDS, leverKindWords } from './leverKindWords'

describe('leverKindWords', () => {
  it('reads the battery lever as "Battery duration"', () => {
    expect(leverKindWords('storage_duration')).toBe('Battery duration')
  })

  it('an unknown kind reads with spaces, not the raw id', () => {
    expect(leverKindWords('dsr_share')).toBe('dsr share')
  })

  it('parity: every kind the backend levers module builds has words', () => {
    const src = readFileSync(resolve(__dirname, '../../../../backend/services/adequacy/levers.py'), 'utf8')
    const kinds = new Set([...src.matchAll(/kind == "([a-z_]+)"/g)].map(m => m[1]))
    const defaults = /DEFAULT_LEVER_KINDS[^=]*=\s*\(([^)]*)\)/.exec(src)?.[1] ?? ''
    for (const m of defaults.matchAll(/"([a-z_]+)"/g)) kinds.add(m[1])
    expect(kinds.size).toBeGreaterThanOrEqual(3)
    for (const k of kinds) expect(Object.keys(LEVER_KIND_WORDS)).toContain(k)
  })
})
