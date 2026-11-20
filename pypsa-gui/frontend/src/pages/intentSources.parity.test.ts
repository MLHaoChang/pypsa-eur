// The source words cover every kind the backend sends (`services/compare/intent_inputs.py` KINDS).
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { SOURCE_WORDS, sourceWord, usedWord } from './intentSources'

describe('SOURCE_WORDS', () => {
  it('has a word for each backend kind', () => {
    const src = readFileSync(resolve(__dirname, '../../../backend/services/compare/intent_inputs.py'), 'utf8')
    const tuple = /^KINDS = \(([^)]*)\)/m.exec(src)
    expect(tuple).not.toBeNull()
    const kinds = [...(tuple as RegExpExecArray)[1].matchAll(/"([a-z_]+)"/g)].map(m => m[1])
    expect(kinds.length).toBeGreaterThan(0)
    for (const k of kinds) expect(SOURCE_WORDS).toHaveProperty(k)
  })
  it('says a typed value is entered here, whatever its kind', () => {
    expect(sourceWord('assumed', true)).toBe('entered here')
    expect(sourceWord('assumed', false)).toBe('assumed')
    expect(sourceWord(undefined, false)).toBeNull()
  })
  it('reads a job record source as the same word', () => {
    expect(usedWord('request')).toBe('entered here')
    expect(usedWord('assumed: a typical load power factor')).toBe('assumed')
    expect(usedWord("the DC load's dynamic envelope")).toBe('load envelope')
    expect(usedWord('project')).toBe('project')
  })
})
