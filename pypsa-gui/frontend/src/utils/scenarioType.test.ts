// The `[type]` prefix is a shared ENCODING: written by the scenario-create
// dialog, read back by three separate surfaces. It lived inside ScenariosPanel
// — the only screen that knew to strip it — so every other one printed the
// marker at the user. These pin the round-trip and the cases where a
// description must survive untouched.
import { describe, expect, it } from 'vitest'
import { parseScenType, scenDescriptionText, tagScenType, SCEN_TYPES } from './scenarioType'

describe('tagScenType / parseScenType round-trip', () => {
  it('round-trips every type with a description', () => {
    for (const type of SCEN_TYPES) {
      expect(parseScenType(tagScenType(type, 'cut the gas fleet')))
        .toEqual({ type, text: 'cut the gas fleet' })
    }
  })

  it('round-trips a type with no description at all', () => {
    // The common case, and the one that leaked: the dialog writes "[scenario]"
    // on its own when the user types nothing, and a raw render showed exactly
    // that string as the project's description.
    for (const type of SCEN_TYPES) {
      const stored = tagScenType(type, '')
      expect(stored).toBe(`[${type}]`)
      expect(parseScenType(stored)).toEqual({ type, text: '' })
      expect(scenDescriptionText(stored)).toBe('')
    }
  })
})

describe('parseScenType', () => {
  it('reports no type for a plain description and returns it whole', () => {
    expect(parseScenType('just some notes')).toEqual({ type: null, text: 'just some notes' })
  })

  it('leaves an unrecognised bracketed word alone', () => {
    // A description may legitimately open with a bracket. A looser pattern
    // would eat "[draft]" and silently drop the user's first word.
    expect(parseScenType('[draft] cut the gas fleet'))
      .toEqual({ type: null, text: '[draft] cut the gas fleet' })
  })

  it('only matches the tag at the start', () => {
    expect(parseScenType('see [stress] for the comparison'))
      .toEqual({ type: null, text: 'see [stress] for the comparison' })
  })

  it('handles null, undefined and empty as no description', () => {
    for (const v of [null, undefined, '']) {
      expect(parseScenType(v)).toEqual({ type: null, text: '' })
    }
  })

  it('keeps a multi-line description intact after the tag', () => {
    expect(parseScenType('[stress] line one\nline two').text).toBe('line one\nline two')
  })
})

describe('scenDescriptionText', () => {
  it('is falsy exactly when there is nothing to render', () => {
    // Callers gate their <p> on truthiness, so a tag-only description must
    // come back '' rather than the raw marker.
    expect(scenDescriptionText('[baseline]')).toBeFalsy()
    expect(scenDescriptionText(null)).toBeFalsy()
    expect(scenDescriptionText('[baseline] reference run')).toBeTruthy()
    expect(scenDescriptionText('plain notes')).toBeTruthy()
  })

  it('never returns a string still carrying a type marker', () => {
    for (const type of SCEN_TYPES) {
      expect(scenDescriptionText(`[${type}] whatever`)).not.toContain(`[${type}]`)
    }
  })
})
