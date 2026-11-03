import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { TABS } from '../Results'
import { NOT_KEPT_WORDS, SECTION_TAB, notKeptSentence, notKeptShort, sectionWords } from './sectionWords'

const label = (id: string) => TABS.find(t => t.id === id)?.label

describe('sectionWords', () => {
  it('names a section by its Results tab label', () => {
    expect(sectionWords('results_capacity', label)).toBe('Capacity expansion')
    expect(sectionWords('results_prices', label)).toBe('Prices')
  })
  it('falls back to plain words for an unknown section', () => {
    expect(sectionWords('results_unit_commitment', label)).toBe('Unit commitment')
  })
  it('covers every retained section the backend keeps', () => {
    const src = readFileSync(resolve(__dirname, '../../../../backend/services/retained_results.py'), 'utf8')
    const keys = [...src.matchAll(/^[A-Z_]+_SECTION = '([a-z_]+)'$/gm)].map(m => m[1])
    keys.push(...[...src.matchAll(/Retained\('([a-z_]+)'/g)].map(m => m[1]))
    expect(keys.length).toBeGreaterThanOrEqual(6)
    for (const k of keys) expect(label(SECTION_TAB[k]), k).toBeTruthy()
  })
})

describe('not_kept words (LC-03 slice 2)', () => {
  it('has words for every reason code the backend stores', () => {
    const py = readFileSync(resolve(process.cwd(), '..', 'backend', 'services', 'not_kept.py'), 'utf8')
    const codes = [...py.matchAll(/^([A-Z_]+) = '([a-z_]+)'$/gm)].filter(m => m[1] !== 'NOT_KEPT').map(m => m[2])
    expect(codes.length).toBeGreaterThanOrEqual(3)
    expect(codes.filter(c => !NOT_KEPT_WORDS[c])).toEqual([])
    const why = py.slice(py.indexOf('WHY:'))
    expect([...why.matchAll(/^ {4}([A-Z_]+):/gm)].map(m => m[1]).sort())
      .toEqual([...py.matchAll(/^([A-Z_]+) = '[a-z_]+'$/gm)].map(m => m[1]).filter(n => n !== 'NOT_KEPT').sort())
  })

  it('names the section by its tab and the reason in words', () => {
    expect(notKeptSentence('results_prices', 'multi_period_not_digested', label))
      .toBe('This run kept no Prices: multi-period runs keep no dispatch or prices. Current results are not shown in their place.')
    expect(notKeptSentence('study_result', 'over_section_cap', label))
      .toBe('This run kept no Study result: too large to keep. Current results are not shown in their place.')
    expect(notKeptShort('multi_period_not_digested')).toBe('multi-period run')
    expect(notKeptShort('something_new')).toBe('could not be kept')
  })
})
