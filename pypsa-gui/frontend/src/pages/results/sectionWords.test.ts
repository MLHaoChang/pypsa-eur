import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { TABS } from '../Results'
import { SECTION_TAB, sectionWords } from './sectionWords'

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
