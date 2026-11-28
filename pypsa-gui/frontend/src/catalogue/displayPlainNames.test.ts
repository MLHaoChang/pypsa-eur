/**
 * expert-catalogue 05 (spec §7.3): the display replacement on the case table it
 * shares with the server's live-voice copy (`tests/test_display_plain_names.py`),
 * and the one word source: a replaced name reads exactly as the catalogue names it.
 */
import { describe, expect, it } from 'vitest'
import table from '../../../backend/tests/data/display_plain_names_cases.json'
import { assistantSpeech, plainTextForSpeech } from '../utils/speechOut'
import { displayPlainNames, displayWord, PANEL_NAMES_DIFFER, plainNameOf, type ModelNameMap } from './displayPlainNames'
import generated from './modelNames.generated.json'
import { snakeNameRule } from './modelNameRule'

interface Case { why: string; text: string; user?: string; want: string }
const cases = (table.cases as Case[]).map((c) => [c.why, c] as const)

describe('display replacement: the shared case table', () => {
  it.each(cases)('%s', (_why, c) => {
    expect(displayPlainNames(c.text, { userText: c.user })).toBe(c.want)
  })

  it.each(cases)('spoken: %s', (_why, c) => {
    expect(assistantSpeech(c.text, c.user)).toBe(plainTextForSpeech(c.want))
  })

  it('covers each kind the spec names', () => {
    const whys = table.cases.map((c) => c.why).join(' | ')
    for (const kind of ['plain name', 'stays raw (p_nom', 'does not hold', 'fenced code', 'bare prose', 'quoted text', 'about the key', 'several tokens']) {
      expect(whys).toContain(kind)
    }
  })
})

describe('display replacement: the catalogue is the one word source', () => {
  const names = generated as ModelNameMap
  const byKey = new Map<string, Set<string>>()
  for (const byName of Object.values(names)) {
    for (const [k, w] of Object.entries(byName)) byKey.set(k, (byKey.get(k) ?? new Set()).add(w))
  }

  it('shows every unambiguous snake_case name as its catalogue word, same casing, and leaves the rest raw', () => {
    const rule = snakeNameRule(byKey.keys())
    let replaced = 0
    for (const [key, words] of byKey) {
      const shown = displayPlainNames(`\`${key}\``)
      if (!rule.test(key)) expect(shown).toBe(`\`${key}\``)
      else if (words.size > 1 || PANEL_NAMES_DIFFER.includes(key)) expect(shown, key).toBe(`\`${key}\``)
      else {
        expect(shown, key).toBe([...words][0])
        replaced++
      }
    }
    expect(replaced).toBeGreaterThan(80)
  })

  it('keeps raw the names the panel labels differently, as the backend test derives them', () => {
    expect([...PANEL_NAMES_DIFFER].sort()).toEqual([...table.panel_names_differ].sort())
  })

  it('reads the words the panel shows (spot checks against the catalogue file)', () => {
    expect(displayWord('e_nom')).toBe(plainNameOf('Store', 'e_nom'))
    expect(displayWord('s_nom')).toBeNull() // the panel labels it differently until slice 03
    expect(displayWord('p_set')).toBe(plainNameOf('Load', 'p_set'))
    expect([plainNameOf('Store', 'e_nom'), plainNameOf('Load', 'p_set')])
      .toEqual(['Energy capacity', 'Demand'])
    expect(displayWord('p_nom')).toBeNull()
  })
})
